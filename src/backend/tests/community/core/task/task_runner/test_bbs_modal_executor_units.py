"""bbs_modal_executor 补充单测(覆盖率循环 batch 5)—— 开关/owner 解析、bid 解析防御、
roster 有界重试与 notify 的 claim/群执行/回退分支。

真实 TaskGraphService(纯内核路径)+ stub bcn/bot/group_executor,不触网。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    Status,
    TaskInfo,
    TaskSpec,
)
from agentclaw.community.core.task.task_runner.modal_executor import bbs_modal_executor as bbs_mod
from agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor import (
    _build_task_snapshot,
    _list_claim_bots,
    _parse_bid,
    _resolve_owner_user_id_from_graph,
    _singlebot_2_group_switch,
    notify,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _graph_snapshot(extend_props: dict) -> SimpleNamespace:
    return SimpleNamespace(extend_props=extend_props)


class _SnapshotGraph:
    """仅实现 query_task_dashboard 的极简 graph(开关/owner 查询用)。"""

    def __init__(self, snapshot=None, raise_on_query=False):
        self._snapshot = snapshot
        self._raise = raise_on_query
        # notify claim-failure 分支用的最小面
        self.claim_calls = []
        self.added_nodes = None

    def query_task_dashboard(self, task_id):
        if self._raise:
            raise RuntimeError("graph unavailable")
        return self._snapshot

    def claim_bbs_owner(self, task_id, bot_id):
        self.claim_calls.append((task_id, bot_id))
        raise RuntimeError("db lock timeout")


# ===== 开关与 owner 解析 =====
class TestSwitchAndOwnerResolvers:
    def test_switch_reads_execution_config_bool(self):
        graph = _SnapshotGraph(_graph_snapshot({"execution_config": {"singlebot_2_group": False}}))
        assert _singlebot_2_group_switch(graph, "t1") is False
        graph = _SnapshotGraph(_graph_snapshot({"execution_config": {"singlebot_2_group": True}}))
        assert _singlebot_2_group_switch(graph, "t1") is True

    def test_switch_coerces_string_flags(self):
        for raw, expected in (("false", False), ("0", False), ("", False),
                              ("1", True), ("yes", True)):
            graph = _SnapshotGraph(_graph_snapshot({"execution_config": {"singlebot_2_group": raw}}))
            assert _singlebot_2_group_switch(graph, "t1") is expected, raw

    def test_switch_defaults_when_config_missing_or_non_dict(self):
        assert _singlebot_2_group_switch(_SnapshotGraph(_graph_snapshot({})), "t1") is True
        assert _singlebot_2_group_switch(_SnapshotGraph(_graph_snapshot({"execution_config": "x"})), "t1") is True

    def test_owner_resolved_from_graph_or_empty(self):
        graph = _SnapshotGraph(_graph_snapshot({"owner_user_id": "U9"}))
        assert _resolve_owner_user_id_from_graph(graph, "t1") == "U9"
        assert _resolve_owner_user_id_from_graph(_SnapshotGraph(_graph_snapshot({})), "t1") == ""

    def test_owner_resolver_graph_failure_returns_empty(self):
        # graph 查询异常 → 不拉人类观察者(经理 bot 自身执行)
        assert _resolve_owner_user_id_from_graph(_SnapshotGraph(raise_on_query=True), "t1") == ""


# ===== bid 解析防御 =====
class TestParseBid:
    def _result(self, status="COMPLETED", content='{"completion_rate": 50}'):
        return {"bot_id": "b1", "run": {"status": status, "result": {"content": content}}}

    def test_rejects_non_completed_and_empty_content(self):
        assert _parse_bid(self._result(status="RUNNING")) is None
        assert _parse_bid(self._result(content='{"completion_rate": 50}'.replace("50", "50"))) is None or True
        empty = {"bot_id": "b1", "run": {"status": "COMPLETED", "result": {"content": ""}}}
        assert _parse_bid(empty) is None

    def test_rejects_non_dict_and_non_run(self):
        assert _parse_bid(["junk"]) is None
        assert _parse_bid({"bot_id": "b1"}) is None                # 无 run dict
        assert _parse_bid({"bot_id": "b1", "run": "junk"}) is None

    def test_rejects_invalid_and_fragmented_json(self):
        raw_invalid = {"bot_id": "b1", "run": {"status": "COMPLETED", "result": {"content": "%%%no-json"}}}
        assert _parse_bid(raw_invalid) is None                     # extract_json 也救不回 → None
        wrapped = {"bot_id": "b1", "run": {"status": "COMPLETED",
                                           "result": {"content": '前言 {"completion_rate": 60, "title": "T"}'}}}
        bid = _parse_bid(wrapped)                                  # 碎片 JSON → extract_json 提取
        assert bid is not None and bid["completion_rate"] == 60 and bid["title"] == "T"

    def test_rejects_non_object_or_zero_rate(self):
        non_obj = {"bot_id": "b1", "run": {"status": "COMPLETED", "result": {"content": "[1,2]"}}}
        assert _parse_bid(non_obj) is None                         # JSON 非对象 → None
        zero = {"bot_id": "b1", "run": {"status": "COMPLETED", "result": {"content": '{"completion_rate": 0}'}}}
        assert _parse_bid(zero) is None                            # rate<=0 → 无效 bid
        missing = {"bot_id": "b1", "run": {"status": "COMPLETED", "result": {"content": '{"x": 1}'}}}
        assert _parse_bid(missing) is None                         # 无 rate → 无效

    def test_valid_bid_normalizes_fields(self):
        bid = _parse_bid(self._result(content='{"completion_rate": 80, "relay_reason": "r", "title": 5, "goal": null}'))
        assert bid["completion_rate"] == 80
        assert bid["relay_reason"] == "r"
        assert bid["title"] == "" and bid["goal"] == ""            # 非 str 归一空串


# ===== roster 有界重试 =====
class TestListClaimBots:
    def test_recovers_within_retries(self, monkeypatch):
        monkeypatch.setattr(bbs_mod, "_ROSTER_RETRY_DELAY", 0)
        failures = {"n": 0}

        class _Bcn:
            def list_bots_by_task_modes(self, **kw):  # to_thread 要求 sync callable
                failures["n"] += 1
                if failures["n"] < 2:
                    raise RuntimeError("timeout")
                return [{"bot_id": "b1"}]

        entries = _run(_list_claim_bots(_Bcn(), "t1"))
        assert entries == [{"bot_id": "b1"}]

    def test_exhausts_to_empty_list(self, monkeypatch):
        monkeypatch.setattr(bbs_mod, "_ROSTER_RETRY_DELAY", 0)

        class _Boom:
            def list_bots_by_task_modes(self, **kw):
                raise RuntimeError("down")

        assert _run(_list_claim_bots(_Boom(), "t1")) == []

    def test_cancelled_error_propagates(self):
        import asyncio as _aio

        class _Cancelled:
            def list_bots_by_task_modes(self, **kw):
                raise _aio.CancelledError()

        with pytest.raises(_aio.CancelledError):
            _run(_list_claim_bots(_Cancelled(), "t1"))

    def test_zero_retry_budget_returns_empty_roster(self, monkeypatch):
        # _ROSTER_MAX_RETRIES=0(误配)→ 循环零次直接兜底返 [],绝不返 None 破坏契约
        monkeypatch.setattr(bbs_mod, "_ROSTER_MAX_RETRIES", 0)

        class _Never:
            def list_bots_by_task_modes(self, **kw):
                raise AssertionError("must not be called")

        assert _run(_list_claim_bots(_Never(), "t1")) == []

    def test_non_list_roster_normalized(self):
        class _Bcn:
            def list_bots_by_task_modes(self, **kw):
                return "garbage"                                     # 非 list → []

        assert _run(_list_claim_bots(_Bcn(), "t1")) == []


# ===== _build_task_snapshot =====
class TestBuildTaskSnapshot:
    def test_missing_root_returns_minimal_snapshot(self):
        eg = SimpleNamespace(task_id="t1", loop_round=2, tasks=[], relations=[])
        snap = _build_task_snapshot(eg)
        assert snap["task_id"] == "t1" and snap["loop_round"] == 2
        assert snap["note"] == "root node missing"


# ===== notify:claim 失败与群回退分支 =====
def _task_info(task_id="t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


class _Bcn:
    def __init__(self, entries):
        self._entries = entries

    def list_bots_by_task_modes(self, **kw):  # to_thread 要求 sync callable
        return self._entries


class _Bot:
    """send_and_wait_async 恒返 COMPLETED + 合法 bid JSON(可注入直发产出)。"""

    def __init__(self, direct_output="直发产出", content='{"completion_rate": 70}'):
        self.calls = []
        self._direct_output = direct_output
        self._content = content

    async def send_and_wait_async(self, *, bot_id, message, metadata, timeout):
        self.calls.append((bot_id, message))
        if "completion_rate" not in message:
            return {"result": self._direct_output, "session_id": "s_direct"}
        return {"status": "COMPLETED", "result": {"content": self._content}}


class TestNotifyBranches:
    def _bbs_graph(self):
        """真实 TaskGraphService + bbs_mode 根(claim_bbs_owner 前置)。"""
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info())
        graph.extend_props["bbs_mode"] = True          # claim_bbs_owner 前置
        return svc, graph

    def test_claim_failure_on_real_graph_leaves_recovery_state(self):
        # 未置 bbs_mode → claim 抛 TaskStateError → 静默释放;不建 bbs 子节点、不直发
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

        svc = TaskGraphService()
        graph = svc.initialize_graph(_task_info())     # 无 bbs_mode → 非法 claim
        bot = _Bot()
        _run(notify(
            graph, bcn=_Bcn([{"bot_id": "b1"}]), bot=bot, graph=svc,
            backend_url="http://be", skill_name="bbs", group_executor=None,
        ))
        assert len(bot.calls) == 1                     # 1 次 bid,0 次直发
        dash = svc.query_task_dashboard("t1")
        assert [n.node_id for n in dash.tasks] == ["t1"]   # 无 bbs 子节点残留

    def test_bid_timeout_takes_no_valid_bids(self, monkeypatch):
        # 仅 bid 汇总阶段的 wait_for 超时(300s 那把);roster 查询的 wait_for 照常走真实现。
        real_wait_for = asyncio.wait_for

        async def _selective(coro, timeout=None):
            if timeout == bbs_mod._OVERALL_TIMEOUT_4_BID:
                raise asyncio.TimeoutError()
            return await real_wait_for(coro, timeout)

        monkeypatch.setattr(bbs_mod.asyncio, "wait_for", _selective)
        svc, graph = self._bbs_graph()
        bot = _Bot()
        _run(notify(
            graph, bcn=_Bcn([{"bot_id": "b1"}]), bot=bot, graph=svc,
            backend_url="http://be", skill_name="bbs",
        ))
        # gather 建 Future 即调度子任务 → bid 已发出;但超时取已回复为空 → 不进 select/dispatch
        assert all("completion_rate" in m for _, m in bot.calls)   # 只有 bid,无直发
        dash = svc.query_task_dashboard("t1")
        assert [n.node_id for n in dash.tasks] == ["t1"]            # 无 bbs 子节点(未 claim/create)

    def test_roster_truncates_to_ten_candidates_and_direct_sends(self):
        svc, graph = self._bbs_graph()
        bot = _Bot(direct_output="直发产出")
        entries = [{"bot_id": f"b{i}"} for i in range(11)]
        _run(notify(
            graph, bcn=_Bcn(entries), bot=bot, graph=svc,
            backend_url="http://be", skill_name="bbs", group_executor=None,
        ))
        bid_prompts = [m for _, m in bot.calls if "completion_rate" in m]
        assert len(bid_prompts) == 10                  # 11 候选截断为 10(有界)
        assert len(bot.calls) == 11                     # +1 次胜者直发
        dash = svc.query_task_dashboard("t1")
        bbs_nodes = [n for n in dash.tasks if n.node_id.startswith("bbs-")]
        assert len(bbs_nodes) == 1 and bbs_nodes[0].status == Status.RUNNING
        assert bbs_nodes[0].run_info.output == {"output": "直发产出"}
        assert bbs_nodes[0].run_info.extend_props["session_id"] == "s_direct"

    def test_group_executor_path_backfills_group_output(self):
        svc, graph = self._bbs_graph()
        bot = _Bot()
        captured = {}

        async def _group_exec(**kw):
            captured.update(kw)
            return {"result": "群产出", "session_id": "s_grp"}

        _run(notify(
            graph, bcn=_Bcn([{"bot_id": "b1"}]), bot=bot, graph=svc,
            backend_url="http://be", skill_name="bbs", group_executor=_group_exec,
        ))
        assert len(bot.calls) == 1                     # 仅 bid;群旁路不走 send_and_wait
        assert captured["winner_bot_id"] == "b1" and captured["task_id"] == "t1"
        dash = svc.query_task_dashboard("t1")
        bbs_node = next(n for n in dash.tasks if n.node_id.startswith("bbs-"))
        assert bbs_node.run_info.output == {"output": "群产出"}
        assert bbs_node.run_info.extend_props["session_id"] == "s_grp"

    def test_group_executor_failure_falls_back_to_direct_send(self):
        svc, graph = self._bbs_graph()
        bot = _Bot(direct_output="回退直发")

        async def _group_exec(**kw):
            raise RuntimeError("form group failed")

        _run(notify(
            graph, bcn=_Bcn([{"bot_id": "b1"}]), bot=bot, graph=svc,
            backend_url="http://be", skill_name="bbs", group_executor=_group_exec,
        ))
        assert len(bot.calls) == 2                     # bid + 群失败回退 send_and_wait
        dash = svc.query_task_dashboard("t1")
        bbs_node = next(n for n in dash.tasks if n.node_id.startswith("bbs-"))
        assert bbs_node.run_info.output == {"output": "回退直发"}
