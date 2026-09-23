"""task_runner client/modal_executor 行覆盖缺口补齐测试。

只新建本文件(不改既有测试/源码);fake 就地定义,风格对齐
tests/community/core/task/task_runner/integration/test_bbs_runner.py 与
tests/community/core/task/task_trajectory/test_trajectory_service.py 的敌意协作对象做法:
- 每个用例真实断言行为(返回值/异常类型/落报表副作用);
- 防御分支用敌意 fake(抛错/脏数据)驱动,不 mock 内部函数结果。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.domain.prompt_constants import (
    NO_WEB_SEARCH_CONSTRAINT,
    OUTPUT_LANGUAGE_CONSTRAINT,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import (
    BotServiceBcsBotIdentityResolver,
)
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupResult,
    BcsRateLimitError,
    _map_status as _bcs_map_status,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
    _bcn_node_status,
    _bcn_run_id_as_int,
    _build_bcn_execution_graph,
    _build_claw_mind_execution_graph,
    _manager_worker_status,
    _parse_dict,
    _parse_json,
    _to_ms,
)
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    CandidateSearchResult,
    search_candidates,
)
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiAuthError,
    OpenApiBadRequestError,
    OpenApiError,
    OpenApiRateLimitError,
    _map_status as _openapi_map_status,
    _resp_summary,
)
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    PromptFormatterImpl,
    _RunnerContextBuilder,
    format_benchmark_prompt,
)
from agentclaw.community.core.task.task_runner.client.translators import (
    SingleBotRunTranslator,
    _cb,
)
from agentclaw.community.core.task.task_runner.modal_executor import bbs_modal_executor
from agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor import (
    _build_task_snapshot,
    _emit_bbs_trajectory,
    _list_claim_bots,
    _notify_impl,
    _parse_bid,
    _relay_bbs_execution_result_missing,
    _relay_claim_instruction,
    _resolve_owner_user_id_from_graph,
    _singlebot_2_group_switch,
    notify,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_bbs import (
    TaskExecutorBbsMixin,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    BcsGroupHandle,
    SingleBotHandle,
    TaskExecutorResultPoller,
)


# ===== 通用共享 helper(真实领域模型,零 mock) =====

def _spec(objective: str = "整理基础架构方向架构师名册") -> TaskSpec:
    return TaskSpec(
        context=Context(title="架构师名册", background="基础架构方向"),
        goal=Goal(objective=objective, acceptances=[AcceptanceCriteria("ac1", "给出结论")]),
    )


def _node(node_id: str = "c1", task_id: str = "t1", *, run_mode="single_bot",
          assignee="b1", extend_props=None, node_run_graph=None,
          status=Status.PENDING) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=status,
        task_spec=_spec(),
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee,
                             extend_props=extend_props or {}),
        node_run_graph=node_run_graph,
    )


def _execution_graph(task_id: str = "t1") -> TaskExecutionGraph:
    """最小真实执行图:一个 HUNG 根(root.node_id == task_id)。"""
    root = TaskNode(
        node_id=task_id, task_id=task_id, status=Status.HUNG,
        task_spec=_spec(), run_info=RuntimeInfo(), node_run_graph=None,
    )
    return TaskExecutionGraph(
        run_id=1, loop_round=2, status=Status.HUNG, tasks=[root], task_id=task_id,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ============================================================================
# client/prompt_formatter.py
# ============================================================================

class TestPromptFormatter:
    def test_format_verify_renders_acceptances_and_child_outputs(self):
        formatter = PromptFormatterImpl()
        from agentclaw.community.core.task.domain.models import AcceptanceCriteria as _Acc
        out = formatter.format_verify(
            {"child_outputs": {"c1": {"o": 1}}, "acceptances": [_Acc("a1", "d1"), _Acc("a2", "d2")]},
            _node(),
        )
        assert out.startswith("验收标准:d1;d2")
        assert "子产出:{'c1': {'o': 1}}" in out
        assert out.endswith(NO_WEB_SEARCH_CONSTRAINT)

    def test_runner_context_builder_delegates_to_runner(self):
        class _RecordingRunner:
            def __init__(self):
                self.calls = []

            def _build_context(self, task_id, node_id):
                self.calls.append((task_id, node_id))
                return {"mode": "execute"}

        runner = _RecordingRunner()
        builder = _RunnerContextBuilder(runner)
        assert builder.build("t1", "n1") == {"mode": "execute"}
        assert runner.calls == [("t1", "n1")]


class TestFormatBenchmarkPrompt:
    def test_empty_spec_keeps_only_constraints(self):
        out = format_benchmark_prompt({})
        assert out == (
            "/task [TASK-LOOP-EVALUATION] "
            + "\n\n".join([NO_WEB_SEARCH_CONSTRAINT, OUTPUT_LANGUAGE_CONSTRAINT])
        )

    def test_full_spec_renders_every_section_and_branch(self):
        spec = {
            "metadata": {"title": "性能基准", "instruction": "完成压测"},
            "context": {
                "background": "测试背景",
                "extend_props": {
                    "key_abilities": ["压测", "调优"],
                    "chain_order": [
                        {"step": 1, "name": "调研", "rationale": "先摸底", "modality_type": "single_bot"},
                        {"step": 2, "name": "清理", "rationale": "", "modality_type": ""},
                    ],
                    "benchmark_task_id": "BENCH-1",
                    "business_type": "charity",
                },
            },
            "goal": {
                "objective": "产出报告",
                "acceptances": [
                    {"id": "a1", "acceptance": "覆盖验收 1"},   # 请求 DTO 字段名
                    {"id": "a2", "description": "覆盖验收 2"},  # 响应 DTO 字段名
                ],
            },
        }
        out = format_benchmark_prompt(spec)
        assert out.startswith("/task [TASK-LOOP-EVALUATION] ")
        assert "# 性能基准" in out
        assert "## 执行指令\n完成压测" in out
        assert "## 任务背景\n测试背景" in out
        assert "## 任务目标\n产出报告" in out
        assert "## 验收标准\n- [a1] 覆盖验收 1\n- [a2] 覆盖验收 2" in out
        assert "## 关键能力要求\n- 压测\n- 调优" in out
        assert "## 交接链路" in out
        assert "### 步骤 1: 调研 (类型: single_bot)\n先摸底" in out
        assert "### 步骤 2: 清理\n\n" in out or "### 步骤 2: 清理" in out
        assert "**Benchmark 任务 ID**: BENCH-1" in out
        assert "**任务类型**: charity" in out
        assert NO_WEB_SEARCH_CONSTRAINT in out
        assert OUTPUT_LANGUAGE_CONSTRAINT in out


# ============================================================================
# client/callback_data_enricher.py
# ============================================================================

class TestEnricherHelpers:
    def test_parse_json_all_input_families(self):
        assert _parse_json({"a": 1}) == {"a": 1}
        assert _parse_json('{"b": 2}') == {"b": 2}
        assert _parse_json("not json") is None
        assert _parse_json("not json", {"d": 1}) == {"d": 1}
        assert _parse_json(None) is None
        assert _parse_json(42) is None

    def test_parse_dict_coerces_only_dict_shapes(self):
        assert _parse_dict({"a": 1}) == {"a": 1}
        assert _parse_dict('{"a": 1}') == {"a": 1}
        assert _parse_dict('[1, 2]') == {}
      # 非 dict JSON / 非 dict 值一律降级 {}
        assert _parse_dict(None) == {}
        assert _parse_dict(7) == {}

    def test_to_ms_rejects_non_numeric(self):
        assert _to_ms(None) is None
        assert _to_ms("abc") is None

    def test_manager_worker_status_projections(self):
        assert _manager_worker_status("task.completed") is Status.DONE
        assert _manager_worker_status("session.completed") is Status.DONE
        assert _manager_worker_status("group.created") is Status.RUNNING
        assert _manager_worker_status("task.assigned") is Status.RUNNING

    def test_bcn_node_status_all_buckets(self):
        assert _bcn_node_status("completed") is Status.DONE
        assert _bcn_node_status("failed") is Status.FAILED
        assert _bcn_node_status("aborted") is Status.CANCELLED
        assert _bcn_node_status("whatever") is Status.RUNNING

    def test_bcn_run_id_as_int(self):
        assert _bcn_run_id_as_int(5) == 5
        assert _bcn_run_id_as_int(None) == 0
        assert _bcn_run_id_as_int("run-abc") == 0
        assert _bcn_run_id_as_int("11") == 11

    def test_build_claw_mind_graph_skips_dirty_node_entries(self):
        graph = _build_claw_mind_execution_graph(
            {
                "flow_runs": {"id": "3", "status": "completed"},
                "node_executions": ["junk-entry", {"no_node_id": True},
                                    {"node_id": "n1", "status": "completed"}],
            },
            run_status="running",
        )
        assert graph is not None
        assert len(graph["tasks"]) == 1
        assert graph["tasks"][0]["node_id"] == "n1"
        assert graph["tasks"][0]["status"] == Status.DONE.value

    def test_build_bcn_graph_falls_back_to_run_detail_nodes(self):
        graph = _build_bcn_execution_graph(
            event_type="state_machine.run.completed",
            run_id=5,
            data=None,
            run_detail={
                "run": {"status": "completed", "output": {"done": 1}},
                "nodes": [{"node_id": "nx", "status": "failed",
                           "started_at": 1, "completed_at": 2,
                           "artifact_text": "art"}],
            },
            graph_detail={"nodes": [], "edges": [{"src": "a", "dst": "nx"}]},
        )
        assert graph is not None
        assert graph["run_id"] == 5
        assert len(graph["tasks"]) == 1  # DAG 无 nodes → 用 run_detail 执行 nodes 建图
        assert graph["tasks"][0]["node_id"] == "nx"
        assert graph["tasks"][0]["status"] == Status.FAILED.value


class _ExplodingFetcher(CallbackDataEnricher):
    """敌意 fetch:BCS 明细/DAG 查询整体抛错(不阻断回投,兜底建图)。"""

    async def _fetch_run_and_graph(self, run_id):
        raise RuntimeError("bcs exploded")


class TestCallbackDataEnricher:
    def test_enrich_bcn_non_dict_data_returns_none(self):
        enricher = CallbackDataEnricher(None)
        cd = TaskCallbackData(data="raw-not-a-dict")
        assert _run(enricher.enrich_bcn(cd, {"event_type": "x"}, "r1")) is None
        assert "execution_graph" not in cd.data

    def test_enrich_bcn_fetch_failure_still_builds_event_body_graph(self):
        enricher = _ExplodingFetcher(None)
        cd = TaskCallbackData(data={"workflow_instance_id": "sid-1"})
        raw = {"event_type": "state_machine.run.completed",
               "data": {"output": {"final": "产出"}}}
        run_detail = _run(enricher.enrich_bcn(cd, raw, "run-9"))
        assert run_detail is None  # fetch 失败 → 无明细可收敛
        eg = cd.data["execution_graph"]  # 兜底极简图,永不为原始事件体
        assert eg["status"] == Status.DONE.value
        assert eg["output"] == {"final": "产出"}
        assert eg["tasks"] == []
        assert "result" not in cd.data  # 无 run 明细,不落 extend_props._ext_info

    def test_enrich_claw_mind_non_dict_data_is_passthrough(self):
        enricher = CallbackDataEnricher(None)
        cd = TaskCallbackData(data=42)
        enricher.enrich_claw_mind(cd, {"ext_info": {"flow_runs": {"id": 1}}})
        assert cd.data == 42  # 非 dict 不解析、不落库


# ============================================================================
# client/translators.py
# ============================================================================

class TestTranslators:
    def test_cb_keeps_legacy_fail_detail_field(self):
        cd = _cb("t::n", "single_bot", success=False, fail_detail="legacy boom")
        assert cd.data["result"] == {"success": False, "fail_detail": "legacy boom"}

    def test_completed_non_object_terminal_makes_exec_error(self):
        cd = SingleBotRunTranslator.adapt(
            {"status": "completed", "result": {"content": "[1,2]"}}, "t::n")
        res = cd.data["result"]
        assert res["success"] is False
        assert "terminal result must be a JSON object" in res["exec_error"]

    def test_completed_without_gaps_defaults_to_empty(self):
        cd = SingleBotRunTranslator.adapt(
            {"status": "completed", "result": {"content": '{"success": true, "data": "ok"}'}},
            "t::n")
        assert cd.data["result"]["success"] is True
        assert cd.data["result"]["gaps"] == []
        assert cd.data["result"]["data"] == "ok"

    def test_completed_non_list_gaps_makes_exec_error(self):
        cd = SingleBotRunTranslator.adapt(
            {"status": "completed", "result": {"content": '{"success": false, "gaps": "缺信息"}'}},
            "t::n")
        res = cd.data["result"]
        assert res["success"] is False
        assert "gaps must be a list of strings" in res["exec_error"]


# ============================================================================
# client/open_api_bot_adapter.py + client/bcs_http_adapter.py 状态映射
# ============================================================================

class _HostileResponse:
    """敌意响应:.text 访问即抛错。"""

    @property
    def text(self):
        raise RuntimeError("unreadable")


def _resp(status: int, text: str = "boom") -> httpx.Response:
    return httpx.Response(status_code=status, text=text)


class TestOpenApiStatusMapping:
    def test_resp_summary_survives_hostile_response(self):
        assert _resp_summary(_HostileResponse()) == "<unreadable>"

    def test_map_status_auth_rate_limit_and_bad_request(self):
        with pytest.raises(OpenApiAuthError):
            _openapi_map_status(_resp(401))
        with pytest.raises(OpenApiAuthError):
            _openapi_map_status(_resp(403))
        with pytest.raises(OpenApiRateLimitError):
            _openapi_map_status(_resp(429))
        with pytest.raises(OpenApiBadRequestError):
            _openapi_map_status(_resp(404))


class TestBcsStatusMapping:
    def test_map_status_rate_limit(self):
        with pytest.raises(BcsRateLimitError):
            _bcs_map_status(_resp(429, "slow down"))


# ============================================================================
# client/candidate_search.py
# ============================================================================

class _FakeDiscover:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def search_by_keyword(self, *, keyword, user_id="", top_k=3, min_score=0.01, filters=None):
        self.calls.append(keyword)
        return {"items": self.items}


class TestCandidateSearch:
    @pytest.mark.asyncio
    async def test_empty_query_returns_no_candidates(self):
        result = await search_candidates(_FakeDiscover([]), "")
        assert isinstance(result, CandidateSearchResult)
        assert result.candidates == [] and result.tokens == []
        assert result.raw_item_count == 0 and result.failed_keywords == []

    @pytest.mark.asyncio
    async def test_missing_discover_returns_no_candidates(self):
        result = await search_candidates(None, "行业数据分析")
        assert result.candidates == []
        assert result.tokens  # token 化了但无 discover 可用

    @pytest.mark.asyncio
    async def test_non_dict_items_are_skipped_not_fatal(self):
        discover = _FakeDiscover([
            {"bot_uuid": "b-1", "recommend": {"score": 0.9}},
            "junk-entry",
            42,  # 全家桶脏数据:非 dict 条目防御跳过
        ])
        result = await search_candidates(discover, "行业数据分析", user_id="u1")
        assert len(result.candidates) == 1
        assert result.candidates[0]["bot_uuid"] == "b-1"
        assert result.failed_keywords == []
        assert discover.calls  # 真发起了检索


# ============================================================================
# client/bcs_bot_identity_resolver.py
# ============================================================================

class _FakeBotService:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def list_bots_by_conditions(self, **kwargs):
        self.calls.append(kwargs)
        return {"items": self._items}


class TestBcsBotIdentityResolver:
    def test_empty_input_rejected(self):
        resolver = BotServiceBcsBotIdentityResolver(_FakeBotService([]))
        with pytest.raises(BotIdentityResolutionError):
            resolver.resolve_many([])
        with pytest.raises(BotIdentityResolutionError):
            resolver.resolve_many(["", "   "])

    def test_ids_with_owner_passthrough_without_lookup(self):
        svc = _FakeBotService([])
        resolver = BotServiceBcsBotIdentityResolver(svc)
        assert resolver.resolve_many(["bot-a:owner-1", "bot-b:owner-2"]) == {
            "bot-a:owner-1": "bot-a:owner-1",
            "bot-b:owner-2": "bot-b:owner-2",
        }
        assert svc.calls == []  # 身份已是 bot:owner → 不查 BotService

    def test_duplicate_authoritative_rows_are_ambiguous(self):
        # 非 dict 行被防御跳过;同 bot_id 两行权威记录 → ambiguous 拒解析
        svc = _FakeBotService([
            "junk-row",
            {"bot_id": "bot-a", "owner_id": "u1"},
            {"bot_id": "bot-a", "owner_id": "u2"},
        ])
        resolver = BotServiceBcsBotIdentityResolver(svc)
        with pytest.raises(BotIdentityResolutionError, match="ambiguous"):
            resolver.resolve_many(["bot-a"])

    def test_single_authoritative_row_resolves_owner(self):
        svc = _FakeBotService([{"bot_id": "bot-a", "owner_id": "u1"}])
        resolver = BotServiceBcsBotIdentityResolver(svc)
        assert resolver.resolve_many(["bot-a"]) == {"bot-a": "bot-a:u1"}
        assert svc.calls[0]["bot_ids"] == ["bot-a"]
        assert svc.calls[0]["page_size"] == 1


# ============================================================================
# modal_executor/bbs_modal_executor.py —— 纯函数
# ============================================================================

class _SnapshotGraph:
    """返回固定 dashboard 快照(或抛错)的 graph 替身;report 记账回执。"""

    def __init__(self, snapshot=None, exc=None):
        self.snapshot = snapshot
        self.exc = exc
        self.reports = []

    def query_task_dashboard(self, task_id):
        if self.exc is not None:
            raise self.exc
        return self.snapshot

    def report(self, data):
        self.reports.append(data)
        return {"success": True}


def _dash(**extend_props):
    return SimpleNamespace(extend_props=dict(extend_props), tasks=[], relations=[])


class TestBbsModalHelpers:
    def test_switch_defaults_when_execution_config_not_dict(self):
        assert _singlebot_2_group_switch(_SnapshotGraph(_dash(execution_config="bogus")), "t") is True
        assert _singlebot_2_group_switch(_SnapshotGraph(exc=RuntimeError("down")), "t") is True

    def test_resolve_owner_defaults_to_empty_on_graph_failure(self):
        owner = _resolve_owner_user_id_from_graph(_SnapshotGraph(exc=RuntimeError("boom")), "t")
        assert owner == ""

    def test_relay_result_missing_all_exit_paths(self):
        node_running = _node("rn1", "t1", status=Status.RUNNING)
        node_plain = _node("rn2", "t1", status=Status.PENDING)
        empty_dash = SimpleNamespace(extend_props={}, tasks=[], relations=[])
        # graph 查询失败 → False(不丢产出,不误判)
        assert _relay_bbs_execution_result_missing(
            _SnapshotGraph(exc=RuntimeError("dash down")), "t1", "rn1") is False
        # 节点缺失 → False:
        assert _relay_bbs_execution_result_missing(
            _SnapshotGraph(empty_dash), "t1", "rn1") is False
        # 节点未 RUNNING → False:
        assert _relay_bbs_execution_result_missing(
            _SnapshotGraph(SimpleNamespace(extend_props={}, tasks=[node_plain], relations=[])),
            "t1", "rn2") is False
        # RUNNING 且 execution_decision 为空 → True(回复了但没汇报事实)
        assert _relay_bbs_execution_result_missing(
            _SnapshotGraph(SimpleNamespace(extend_props={}, tasks=[node_running], relations=[])),
            "t1", "rn1") is True

    def test_emit_trajectory_hostname_retries_garbage_falls_back_attempt_zero(self):
        events = []

        class _RecordingTrajectory:
            def emit_trajectory_event(self, task_id, node_id, phase, **kwargs):
                events.append(kwargs)

        graph = _execution_graph("t-emit")
        _emit_bbs_trajectory(
            _RecordingTrajectory(), graph, None, "bbs_entered",
        )  # 正常节点 extend_props 为空 → 缺省 attempt 0
        graph.tasks[0].run_info.extend_props["harness_retries"] = "not-a-number"
        _emit_bbs_trajectory(
            _RecordingTrajectory(), graph, None, "bbs_entered",
        )
        assert events and all(e["attempt"] == 0 for e in events)

    def test_snapshot_without_root_degrades(self):
        snap = _build_task_snapshot(
            SimpleNamespace(task_id="t-missing", tasks=[], relations=[], loop_round=0))
        assert snap == {"task_id": "t-missing", "loop_round": 0, "note": "root node missing"}

    def test_relay_claim_instruction_all_fields(self):
        text = _relay_claim_instruction(title="范围T", goal="目标G", reason="依据R")
        assert text == "BBS认领范围: 范围T；BBS认领目标: 目标G；BBS认领依据: 依据R"


class TestParseBid:
    def test_non_dict_result_rejected(self):
        assert _parse_bid(None) is None
        assert _parse_bid("junk") is None
        assert _parse_bid({"run": "not-a-dict", "bot_id": "b"}) is None

    def test_non_completed_status_rejected(self):
        assert _parse_bid({"bot_id": "b", "run": {"status": "failed"}}) is None

    def test_empty_content_rejected(self):
        assert _parse_bid({"bot_id": "b", "run": {"status": "COMPLETED", "result": {}}}) is None

    def test_invalid_json_falls_back_to_extract_json(self):
        bid = _parse_bid({"bot_id": "b", "run": {
            "status": "COMPLETED",
            "result": {"content": '前言 {"completion_rate": 70, "relay_reason": "能做"} 后缀'}}})
        assert bid is not None and bid["completion_rate"] == 70
        assert bid["relay_reason"] == "能做"

    def test_unparseable_content_rejected(self):
        assert _parse_bid({"bot_id": "b", "run": {
            "status": "COMPLETED", "result": {"content": "既没有大括号也不像 JSON 的文本"}}}) is None

    def test_non_object_json_rejected(self):
        assert _parse_bid({"bot_id": "b", "run": {
            "status": "COMPLETED", "result": {"content": "[1,2]"}}}) is None

    def test_invalid_completion_rate_rejected(self):
        assert _parse_bid({"bot_id": "b", "run": {
            "status": "COMPLETED", "result": {"content": '{"completion_rate": 0}'}}}) is None
        assert _parse_bid({"bot_id": "b", "run": {
            "status": "COMPLETED", "result": {"content": '{"completion_rate": "high"}'}}}) is None

    def test_completed_status_is_case_insensitive(self):
        bid = _parse_bid({"bot_id": "B", "run": {
            "status": "completed",
            "result": {"content": {"completion_rate": 88, "title": None, "goal": 3}}}})
        assert bid["bot_id"] == "B" and bid["completion_rate"] == 88
        assert bid["title"] == "" and bid["goal"] == ""  # 非字符串 title/goal 归一空串


# ============================================================================
# modal_executor/bbs_modal_executor.py —— notify 全链(fake 风格对齐 test_bbs_runner)
# ============================================================================

def _roster(*bot_ids: str) -> list[dict]:
    return [{"bot_id": bid, "name": bid, "task_claim_mode": True} for bid in bot_ids]


class _RosterBcn:
    def __init__(self, roster, exc=None):
        self.roster = roster
        self.exc = exc
        self.calls = 0

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any", visibility=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return list(self.roster)


class _BbsBot:
    """bid 与 dispatch 共用 send_and_wait_async;延迟可配。"""

    def __init__(self, rates, *, dispatch_raises=False, bid_delay=0.0):
        self._rates = rates
        self.dispatch_raises = dispatch_raises
        self.bid_delay = bid_delay
        self.sent: list[tuple] = []
        self.bid_prompts: list[str] = []

    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                  timeout=180.0, poll_interval=2.0):
        if "[bbs-bid]" in message:
            self.bid_prompts.append(message)
            if self.bid_delay:
                await asyncio.sleep(self.bid_delay)
            rate = self._rates.get(bot_id)
            if rate is None:
                raise RuntimeError("bid error")
            return {"status": "COMPLETED",
                    "result": {"content": json.dumps(
                        {"completion_rate": rate, "relay_reason": f"reason-{bot_id}"})}}
        self.sent.append((bot_id, message, metadata))
        if self.dispatch_raises:
            raise RuntimeError("dispatch failed")
        return {"status": "COMPLETED",
                "result": {"content": "dispatch-result"}, "session_id": "sess-x"}


class _RecordingTrajectory:
    def __init__(self):
        self.events = []

    def emit_trajectory_event(self, task_id, node_id, phase, **kwargs):
        self.events.append((phase, task_id, node_id, kwargs))


class _BbsGraph:
    """记账 claim/加群/补丁的 graph 替身(可配 claim 抛错/不翻态)。"""

    def __init__(self, dashboard=None, *, claim_flips_node=True, claim_raises=False,
                 add_nodes_raises=False):
        self.dashboard = dashboard
        self.claim_flips_node = claim_flips_node
        self.claim_raises = claim_raises
        self.add_nodes_raises = add_nodes_raises
        self.claimed: str | None = None
        self.added_nodes: list = []
        self.report_types: list[str] = []

    def query_task_dashboard(self, task_id):
        if self.dashboard is None:
            raise RuntimeError(f"dashboard unavailable: {task_id}")
        return self.dashboard

    def _find_node(self, node_id):
        for item in self.dashboard.tasks:
            if item.node_id == node_id:
                return item
        return None

    def report(self, data):
        report_type = data.data["report_type"]
        payload = data.data["payload"]
        self.report_types.append(report_type)
        if report_type == "BBS_CLAIM":
            if self.claim_raises:
                raise RuntimeError("claim rejected")
            self.claimed = payload["bot_id"]
            if self.claim_flips_node and self.dashboard is not None:
                node = self._find_node(payload.get("node_id"))
                if node is not None:
                    node.status = Status.RUNNING
                    node.run_info.assignee = payload["bot_id"]
            return {"success": True}
        if report_type == "ADD_NODES":
            if self.add_nodes_raises:
                raise RuntimeError("add_nodes rejected")
            self.added_nodes.extend(payload["nodes"])
            return {"success": True}
        if report_type == "NODE_PATCH":
            patch = payload["patch"]
            if self.dashboard is not None:
                node = self._find_node(patch.node_id)
                if node is not None:
                    if patch.status is not None:
                        node.status = patch.status
                    if patch.assignee is not None or patch.run_mode is not None:
                        node.run_info.assignee = patch.assignee
                    node.run_info.extend_props.update(patch.extend_props_patch or {})
            return {"success": True}
        if report_type == "GRAPH_PATCH":
            if self.dashboard is not None:
                self.dashboard.extend_props.update(payload["patch"].extend_props_patch or {})
            return {"success": True}
        raise AssertionError(report_type)


class TestNotifyFlows:
    def test_roster_truncated_to_ten_bidders(self):
        graph = _BbsGraph(dashboard=_dash(execution_config="bogus"))  # 顺带覆盖非 dict 开关
        bot = _BbsBot({f"bot{i}": 50 for i in range(12)})
        _run(notify(_execution_graph("t-cap"), bcn=_RosterBcn(_roster(*[f"bot{i}" for i in range(12)])),
                    bot=bot, graph=graph, backend_url="http://x"))
        assert len(bot.bid_prompts) == 10  # 12 候选被截断为 10 竞价
        assert graph.claimed is not None
        assert len(bot.sent) == 1  # 只给胜出者发一次任务

    def test_bid_timeout_keeps_recoverable_state(self, monkeypatch):
        monkeypatch.setattr(bbs_modal_executor, "_OVERALL_TIMEOUT_4_BID", 0.05)
        bot = _BbsBot({"A": 80}, bid_delay=0.5)
        graph = _BbsGraph()
        _run(notify(_execution_graph("t-bid-timeout"), bcn=_RosterBcn(_roster("A")),
                    bot=bot, graph=graph, backend_url="http://x"))
        assert bot.sent == []  # bid 超时 → 取已回复(0)→ 无有效竞价,留可恢复态
        assert graph.claimed is None

    def test_claim_failure_leaves_recoverable_state(self):
        bot = _BbsBot({"A": 80})
        graph = _BbsGraph(claim_raises=True)
        _run(notify(_execution_graph("t-claim-fail"), bcn=_RosterBcn(_roster("A")),
                    bot=bot, graph=graph, backend_url="http://x"))
        assert bot.sent == []  # claim 被引擎拒绝 → 不派发、不抛
        assert graph.claimed is None

    def test_relay_target_not_running_after_claim_aborts_dispatch(self):
        g = _execution_graph("t-target-stale")
        relay_node = _node("rn-stale", "t-target-stale", run_mode="bbs")
        g.tasks.append(relay_node)
        graph = _BbsGraph(dashboard=g, claim_flips_node=False)  # claim 未把节点翻 RUNNING
        bot = _BbsBot({"A": 80})
        _run(notify(g, bcn=_RosterBcn(_roster("A")), bot=bot, graph=graph,
                    backend_url="http://x", target_node_id="rn-stale"))
        assert bot.sent == []  # 检测到 relay 目标未 RUNNING → 终止派发
        assert graph.report_types == ["BBS_CLAIM"]

    def test_group_executor_failure_falls_back_to_direct_send(self):
        graph = _BbsGraph()
        bot = _BbsBot({"A": 80})
        group_calls = []

        async def hostile_group_executor(**kwargs):
            group_calls.append(kwargs)
            raise RuntimeError("group infra down")

        _run(notify(_execution_graph("t-group-fail"), bcn=_RosterBcn(_roster("A")),
                    bot=bot, graph=graph, backend_url="http://x",
                    group_executor=hostile_group_executor))
        assert len(group_calls) == 1  # 旁路尝试过一次建群
        assert len(bot.sent) == 1      # 建群失败 → 回退 send_and_wait 直发
        assert bot.sent[0][0] == "A"
        assert "NODE_PATCH" in graph.report_types  # scoped 运行事实仍落图

    def test_relay_bot_silent_on_execution_result_releases_claim(self):
        # 461-477 设计行为:relay bot 回复但未上报 EXECUTION_RESULT → 轨迹行
        # bbs_execution_result_missing(error_type=relay)→ 归还节点回广场。
        # (源曾把 error_type kwarg 传给不收该参的 emit 致 TypeError 被外层兜住,
        # 已修为显式形参——此用例现直接驱动真实路径,不再需要 shim。)
        g = _execution_graph("t-relay-silent")
        relay_node = _node("rn-silent", "t-relay-silent", run_mode="bbs")
        g.tasks.append(relay_node)
        graph = _BbsGraph(dashboard=g, claim_flips_node=True)  # claim 翻 RUNNING
        bot = _BbsBot({"A": 80})
        svc = _RecordingTrajectory()
        _run(notify(g, bcn=_RosterBcn(_roster("A")), bot=bot, graph=graph,
                    backend_url="http://x", target_node_id="rn-silent",
                    task_context_service=svc))
        assert graph.claimed == "A"
        assert len(bot.sent) == 1
        # 回复了但没有 EXECUTION_RESULT → 节点回广场
        assert "NODE_PATCH" in graph.report_types and "GRAPH_PATCH" in graph.report_types
        assert relay_node.run_info.extend_props.get("bbs_owner") is None
        assert relay_node.status is Status.PENDING  # 归还补丁翻回广场态
        assert g.extend_props.get("bbs_mode") is True
        missing = [kw for _, _, _, kw in svc.events
                   if kw["action_result"] == "bbs_execution_result_missing"]
        assert missing, f"no result_missing row: {svc.events}"
        assert missing[0]["error_type"] == "relay"  # 显式 ReasonCatalog 贯通
        assert "EXECUTION_RESULT" in missing[0]["error_msg"]

    def test_result_missing_emit_failure_still_releases_claim(self):
        # 决策 #14 防御深度:轨迹 facade 本身抛错 → 发射器内部吞掉(WARNING),
        # 归还逻辑照常执行、notify 不抛。
        class _HostileTrajectory:
            def emit_trajectory_event(self, *args, **kwargs):
                raise RuntimeError("trajectory down")

        g = _execution_graph("t-relay-noemit")
        relay_node = _node("rn-noemit", "t-relay-noemit", run_mode="bbs")
        g.tasks.append(relay_node)
        graph = _BbsGraph(dashboard=g, claim_flips_node=True)
        bot = _BbsBot({"A": 80})
        _run(notify(g, bcn=_RosterBcn(_roster("A")), bot=bot, graph=graph,
                    backend_url="http://x", target_node_id="rn-noemit",
                    task_context_service=_HostileTrajectory()))  # 不抛
        assert graph.claimed == "A"
        assert "NODE_PATCH" in graph.report_types and "GRAPH_PATCH" in graph.report_types
        assert relay_node.run_info.extend_props.get("bbs_owner") is None

    def test_notify_uncaught_error_is_recorded_then_reraised(self):
        graph = _BbsGraph(add_nodes_raises=True)  # BBS_CLAIM 成功,建 scoped 节点被拒
        bot = _BbsBot({"A": 80})
        svc = _RecordingTrajectory()
        with pytest.raises(RuntimeError, match="add_nodes rejected"):
            _run(notify(_execution_graph("t-notify-boom"), bcn=_RosterBcn(_roster("A")),
                        bot=bot, graph=graph, backend_url="http://x",
                        task_context_service=svc))
        assert bot.sent == []
        assert any(kw["action_result"] == "bbs_notify_failed"
                   for _, _, _, kw in svc.events)  # 轨迹记录后才向上抛


class TestListClaimBots:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        bcn = _RosterBcn([], exc=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await _list_claim_bots(bcn, "t1")

    @pytest.mark.asyncio
    async def test_retries_then_exhausts_and_reports_error(self, monkeypatch):
        monkeypatch.setattr(bbs_modal_executor, "_ROSTER_RETRY_DELAY", 0.0)
        bcn = _RosterBcn([], exc=RuntimeError("roster down"))
        errors = []
        entries = await _list_claim_bots(bcn, "t1", on_error=errors.append)
        assert entries == []
        assert bcn.calls == bbs_modal_executor._ROSTER_MAX_RETRIES
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        assert str(errors[0]) == "roster down"

    @pytest.mark.asyncio
    async def test_zero_retry_budget_returns_empty_without_roster_call(self, monkeypatch):
        # 循环体只以 return/raise 退出;重试预算抹平时唯一的循环外出口
        monkeypatch.setattr(bbs_modal_executor, "_ROSTER_MAX_RETRIES", 0)

        class _NeverCalled:
            def list_bots_by_task_modes(self, **kw):
                raise AssertionError("must not be called")

        assert await _list_claim_bots(_NeverCalled(), "t1") == []

    @pytest.mark.asyncio
    async def test_non_list_roster_normalized_to_empty(self):
        class _WeirdRoster:
            def list_bots_by_task_modes(self, **kw):
                return None  # 合法但形状异常 → 归一 []

        assert await _list_claim_bots(_WeirdRoster(), "t1") == []


# ============================================================================
# modal_executor/task_executor.py
# ============================================================================

class _FakeSettings:
    def __init__(self, enabled=True, exc=None):
        self.enabled = enabled
        self.exc = exc

    def is_enabled(self, flag):
        if self.exc is not None:
            raise self.exc
        return self.enabled


class _DictContext:
    def __init__(self, payload=None, exc=None):
        self.payload = payload or {"mode": "execute", "sibling_outputs": {}}
        self.exc = exc
        self.calls = []

    def build(self, task_id, node_id):
        self.calls.append((task_id, node_id))
        if self.exc is not None:
            raise self.exc
        return dict(self.payload)


class _FakeFormatter:
    def __init__(self):
        self.calls = []

    def format_execute(self, context, node):
        self.calls.append((context, node))
        return "PROMPT"

    def format_verify(self, context, node):
        return "VERIFY"


class _SimpleBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *, bot_id, message, metadata):
        self.sent.append((bot_id, message, metadata))
        return BotSendResult(run_id="r-1", session_id="sess-9")


class _RecordingPoller:
    def __init__(self):
        self.registered = []
        self.stopped = False

    def register(self, handle):
        self.registered.append(handle)

    def stop(self):
        self.stopped = True


class _FakeResolver:
    def __init__(self, mapping=None, exc=None):
        self.mapping = mapping if mapping is not None else {}
        self.exc = exc
        self.calls = []

    def resolve_many(self, ids):
        self.calls.append(list(ids))
        if self.exc is not None:
            raise self.exc
        return dict(self.mapping)


class _FakeBcs:
    def __init__(self, *, create_group_result=None, create_group_exc=None):
        self.create_group_result = create_group_result or BcsCreateGroupResult(
            group_id="g-new", session_id="sess-g", run_id="run-g", definition_ref={"ref": 1})
        self.create_group_exc = create_group_exc
        self.calls = []

    def task_callback_url(self):
        return ""

    async def create_group(self, req):
        self.calls.append(req)
        if self.create_group_exc is not None:
            raise self.create_group_exc
        return self.create_group_result

    async def get_group(self, group_id):
        return {"latest_running_session_id": "sess-latest"}

    async def get_state_machine_run(self, run_id):
        return {"status": "running"}

    async def get_session_messages(self, session_id, **kw):
        return []


def _executor(**overrides):
    defaults = dict(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    defaults.update(overrides)
    return TaskExecutor(**defaults)


class TestTaskExecutorDeviationGuards:
    def test_report_node_patch_without_graph_returns_none(self):
        exe = _executor()
        assert exe._report_node_patch(TaskNodePatch(task_id="t1", node_id="n1")) is None

    def test_skill_report_enabled_defaults_to_true_on_settings_failure(self):
        exe = _executor(task_settings=_FakeSettings(exc=RuntimeError("db down")))
        assert exe._skill_report_enabled() is True
        assert _executor(task_settings=_FakeSettings(enabled=False))._skill_report_enabled() is False

    def test_relay_execution_enabled_graph_missing_or_hostile(self):
        assert _executor()._relay_execution_enabled("t1") is False  # graph 未接 → False
        hostile = _executor(graph=_SnapshotGraph(exc=RuntimeError("boom")))
        assert hostile._relay_execution_enabled("t1") is False
        relay_graph = _executor(graph=_SnapshotGraph(
            _dash(execution_config={"orchestration_mode": "relay"})))
        assert relay_graph._relay_execution_enabled("t1") is True

    def test_singlebot_2_group_switch_defaults_on_hostile_or_dirty_config(self):
        hostile = _executor(graph=_SnapshotGraph(exc=RuntimeError("boom")))
        assert hostile._singlebot_2_group_enabled("t1") is True
        dirty = _executor(graph=_SnapshotGraph(_dash(execution_config="bogus")))
        assert dirty._singlebot_2_group_enabled("t1") is True

    def test_resolve_graph_owner_user_id_survives_hostile_graph(self):
        exe = _executor(graph=_SnapshotGraph(exc=RuntimeError("dash gone")))
        assert exe._resolve_graph_owner_user_id("t1") is None


class TestTaskExecutorDispatch:
    def test_single_bot_bypass_failure_falls_back_to_direct_send(self):
        graph = _SnapshotGraph(_dash(owner_user_id="u-human"))
        bot = _SimpleBot()
        exe = _executor(
            bot=bot,
            bcs=_FakeBcs(),
            formatter=_FakeFormatter(),
            context=_DictContext(),
            graph=graph,
            identity_resolver=_FakeResolver(exc=BotIdentityResolutionError("resolve exploded")),
        )
        assert _run(exe._dispatch_single_bot(_node("c1", "t1"), asyncio.Semaphore(1))) is True
        assert len(bot.sent) == 1  # 建群旁路失败 → 回退 send_message 老链路
        assert "bcs_new" not in bot.sent[0][0]
        assert exe._bcs.calls == []  # 建群从未成功发出 create_group

    def test_single_bot_to_group_registers_poller_when_platform_collects(self):
        exe = _executor(
            poller=_RecordingPoller(),
            context=_DictContext(),
            task_settings=_FakeSettings(enabled=False),  # 平台回收 → 注册 BcsGroupHandle
        )

        async def fake_form(gf):
            return "g-sb2g"

        async def fake_session(gid):
            return "sess-sb2g"

        exe.form_coop_group = fake_form
        exe.get_group_session = fake_session
        node = _node("c1", "t1")
        assert _run(exe._dispatch_single_bot_2_group(node, "b-drv:owner", "owner-1", "t1::c1")) is True
        assert node.run_info.run_mode == "coop_group"
        assert node.run_info.assignee == "g-sb2g"
        assert len(exe._poller.registered) == 1
        handle = exe._poller.registered[0]
        assert isinstance(handle, BcsGroupHandle)
        assert handle.group_id == "g-sb2g" and handle.session_id == "sess-sb2g"

    def test_dispatch_coop_group_survives_hostile_context_builder(self):
        poller = _RecordingPoller()
        exe = _executor(
            bcs=_FakeBcs(),
            poller=poller,
            context=_DictContext(exc=RuntimeError("ctx boom")),
            task_settings=_FakeSettings(enabled=False),
        )
        node = _node("c1", "t1", run_mode="coop_group", assignee=None,
                     extend_props={"group_id": "g9"})
        exe._group_meta["g9"] = {"collab_mode": "chat"}
        assert _run(exe._dispatch_coop_group(node, asyncio.Semaphore(1))) is True
        assert isinstance(poller.registered[0], BcsGroupHandle)

    def test_persist_dispatch_ids_skips_unserializable_request_input(self):
        graph = _PatchGraph()
        exe = _executor(graph=graph)
        circular = {}
        circular["self"] = circular  # json.dumps → ValueError: Circular reference
        exe._persist_dispatch_ids(_node("c1", "t1"), group_id="g-memo",
                                  session_id="s-memo", exec_request_input=circular)
        patch = _last_node_patch(graph)
        assert "group_id" in patch.extend_props_patch
        assert "session_id" in patch.extend_props_patch
        assert "_exec_request_input" not in patch.extend_props_patch  # 序列化失败 → 跳过该键

    def test_persist_dispatch_ids_records_ids_when_serializable(self):
        graph = _PatchGraph()
        exe = _executor(graph=graph)
        exe._persist_dispatch_ids(_node("c1", "t1"), run_id="r-77",
                                  exec_request_input={"query": "PROMPT"})
        patch = _last_node_patch(graph)
        assert "run_id" in patch.extend_props_patch
        assert patch.extend_props_patch["_exec_request_input"] == '{"query": "PROMPT"}'

    def test_aclose_stops_poller(self):
        poller = _RecordingPoller()
        _run(_executor(poller=poller).aclose())
        assert poller.stopped is True


class _PatchGraph:
    """只回读 patch 的 graph 替身(供 _persist_dispatch_ids 断言)。"""

    def __init__(self):
        self.records = []

    def report(self, data):
        self.records.append(data)
        return {"success": True}


def _last_node_patch(graph):
    for data in reversed(graph.records):
        if data.data["report_type"] == "NODE_PATCH":
            return data.data["payload"]["patch"]
    raise AssertionError("no NODE_PATCH reported")


class TestFormCoopGroup:
    def test_rejects_without_bots(self):
        exe = _executor(identity_resolver=_FakeResolver({"a": "uuid-a"}))
        with pytest.raises(BotIdentityResolutionError, match="without bots"):
            _run(exe.form_coop_group(GroupFormation(bot_ids=[], collab_mode="chat")))

    def test_rejects_without_identity_resolver(self):
        exe = _executor()
        with pytest.raises(BotIdentityResolutionError, match="resolver is not configured"):
            _run(exe.form_coop_group(GroupFormation(bot_ids=["a"], collab_mode="chat")))

    def test_rejects_referenced_bot_outside_formation(self):
        exe = _executor(identity_resolver=_FakeResolver({"a": "uuid-a", "ghost": "uuid-g"}))
        gf = GroupFormation(bot_ids=["a"], collab_mode="manager_worker",
                            members_info=[{"bot_id": "a", "role": "manager"}],
                            extend_props={"manager_bot_id": "ghost"})
        with pytest.raises(BotIdentityResolutionError, match="outside GroupFormation.bot_ids"):
            _run(exe.form_coop_group(gf))

    def test_resolver_exception_is_logged_and_reraised(self):
        exe = _executor(identity_resolver=_FakeResolver(exc=RuntimeError("resolver exploded")))
        with pytest.raises(RuntimeError, match="resolver exploded"):
            _run(exe.form_coop_group(GroupFormation(bot_ids=["a"], collab_mode="chat")))

    def test_resolver_omitting_a_bot_is_fatal(self):
        exe = _executor(identity_resolver=_FakeResolver({"a": "uuid-a"}))  # 少给 b
        gf = GroupFormation(bot_ids=["a", "b"], collab_mode="chat",
                            members_info=[{"bot_id": "a", "role": "driver"},
                                          {"bot_id": "b", "role": "consultant"}])
        with pytest.raises(BotIdentityResolutionError, match="omitted bot_id: b"):
            _run(exe.form_coop_group(gf))

    def test_chat_group_with_originator_and_service_spec(self):
        bcs = _FakeBcs()
        exe = _executor(bcs=bcs, identity_resolver=_FakeResolver(
            {"a": "uuid-a", "b": "uuid-b"}), api_base_url="http://api")
        gf = GroupFormation(
            bot_ids=["a", "b"], collab_mode="chat", group_name="调研组",
            members_info=[{"bot_id": "a", "role": "driver"}, {"bot_id": "b", "role": "consultant"}],
            extend_props={
                "originator": "human-boss",
                "originator_bot_id": "a",
                "service_spec": {"tier": 1},
                "task_instruction": "分工执行",
                "task_objective": "产出结论",
            },
        )
        gid = _run(exe.form_coop_group(gf))
        assert gid == "g-new"
        req = bcs.calls[0]
        assert req.originator == "human-boss"      # 显式 originator 优先
        assert req.service_spec == {"tier": 1}
        assert req.driver_bot == "uuid-a"
        assert req.participants[0] == {"bot_uuid": "uuid-a", "role": "driver"}
        assert exe._group_meta["g-new"]["collab_mode"] == "chat"

    def test_originator_falls_back_to_originator_bot_uuid(self):
        bcs = _FakeBcs()
        exe = _executor(bcs=bcs, identity_resolver=_FakeResolver({"a": "uuid-a"}))
        gf = GroupFormation(bot_ids=["a"], collab_mode="chat",
                            extend_props={"originator_bot_id": "a", "task_instruction": "干活"})
        _run(exe.form_coop_group(gf))
        assert bcs.calls[0].originator == "uuid-a"  # 无 originator → 以 originator_bot UUID

    def test_manager_worker_dynamic_protocol_degrades_without_loop_separator(self):
        bcs = _FakeBcs()
        exe = _executor(bcs=bcs, identity_resolver=_FakeResolver({"a": "uuid-a"}),
                        api_base_url="http://api")
        gf = GroupFormation(bot_ids=["a"], collab_mode="manager_worker",
                            members_info=[{"bot_id": "a", "role": "manager"}],
                            extend_props={
                                "dynamic_task_node_protocol": True,
                                "task_instruction": "执行需求",
                                "task_objective": "目标产出",
                                "loop_task_id": "no-colon",  # 缺 "::" → 占位 task/node id
                            })
        gid = _run(exe.form_coop_group(gf))
        assert gid == "g-new"
        req = bcs.calls[0]
        assert req.group_strategy == "manager_worker"
        assert "loop_task_id=<task_id>::<node_id>" in req.context  # 统一节点协议仍成型
        assert "目标产出" in req.context and "执行需求" in req.context
        assert req.event_subscriptions  # skill_report 默认开 → 事件回调订阅在
        assert req.event_subscriptions[0]["sink"]["url"].endswith(
            "/api/v1/collaboration/tasks/callback/report")

    def test_full_execute_envelope_context_gets_reporter_footer_only(self):
        bcs = _FakeBcs()
        exe = _executor(bcs=bcs, identity_resolver=_FakeResolver({"a": "uuid-a"}))
        envelope = "序言\n请严格按以下阶段执行，执行、校验、验收、上报均不可跳过。\n后续"
        gf = GroupFormation(bot_ids=["a"], collab_mode="chat",
                            extend_props={"task_instruction": envelope, "task_objective": "O"})
        _run(exe.form_coop_group(gf))
        ctx = bcs.calls[0].context
        assert ctx.startswith(envelope)  # 已是完整信封 → 只补 reporter 定位脚注
        assert "reporter_bot_id=uuid-a" not in ctx  # reporter 用产品 bot 口径
        assert "[task-execute]" not in ctx.split("请严格按以下阶段执行")[0]

    def test_plain_instruction_without_loop_separator_uses_placeholder_ids(self):
        bcs = _FakeBcs()
        exe = _executor(bcs=bcs, identity_resolver=_FakeResolver({"a": "uuid-a"}))
        gf = GroupFormation(bot_ids=["a"], collab_mode="chat",
                            extend_props={"task_instruction": "原生指令",
                                          "loop_task_id": "no-colon"})
        _run(exe.form_coop_group(gf))
        ctx = bcs.calls[0].context
        assert '"task_id": "<task_id>"' in ctx  # loop_task_id 切分失败 → 占位、不影响收口协议
        assert "原生指令" in ctx

    def test_create_group_failure_propagates(self):
        exe = _executor(bcs=_FakeBcs(create_group_exc=RuntimeError("bcs 500")),
                        identity_resolver=_FakeResolver({"a": "uuid-a"}))
        gf = GroupFormation(bot_ids=["a"], collab_mode="chat",
                            extend_props={"task_instruction": "x"})
        with pytest.raises(RuntimeError, match="bcs 500"):
            _run(exe.form_coop_group(gf))


# ============================================================================
# modal_executor/task_executor_bbs.py
# ============================================================================

class TestBbsMixin:
    def test_group_execution_rejects_empty_winner(self):
        exe = _executor()
        with pytest.raises(BotIdentityResolutionError, match="缺 driver bot"):
            _run(exe._bbs_execute_as_manager_worker_group(
                task_id="t1", node_id="n1", winner_bot_id="",
                owner_user_id=None, task_instruction="i", deadline_monotonic=0))

    def test_group_execution_without_session_raises_timeout(self):
        exe = _executor()

        async def fake_form(gf):
            return "g-nosession"

        async def fake_session(gid):
            return None

        exe.form_coop_group = fake_form
        exe.get_group_session = fake_session
        with pytest.raises(asyncio.TimeoutError, match="无可用 session"):
            _run(exe._bbs_execute_as_manager_worker_group(
                task_id="t1", node_id="n1", winner_bot_id="bot-w",
                owner_user_id=None, task_instruction="i", deadline_monotonic=0))

    def test_run_bbs_delegates_to_notify(self):
        # bcn/bot 未接 → notify 静默留可恢复态;契约是委托不抛
        exe = _executor(graph=_BbsGraph())
        _run(exe.run_bbs(_execution_graph("t-run-bbs")))  # 不抛即行为正确

    def test_state_machine_bindings_rejects_non_mapping(self):
        gf = GroupFormation(bot_ids=["a"], collab_mode="state_machine",
                            extend_props={"participant_bindings": "bogus"})
        with pytest.raises(BotIdentityResolutionError, match="must be a mapping"):
            TaskExecutorBbsMixin._state_machine_bindings(gf)

    def test_state_machine_bindings_rejects_empty_name(self):
        gf = GroupFormation(bot_ids=["a"], collab_mode="state_machine",
                            extend_props={"participant_bindings": {"": ["b"]}})
        with pytest.raises(BotIdentityResolutionError, match="name must not be empty"):
            TaskExecutorBbsMixin._state_machine_bindings(gf)

    def test_state_machine_bindings_accepts_dict_spec_and_string_ids(self):
        gf = GroupFormation(bot_ids=["a"], collab_mode="state_machine",
                            extend_props={"participant_bindings": {
                                "writer": {"bot_ids": "w-bot", "source": "auto"},
                                "editor": ["e-bot"],
                            }})
        bindings = TaskExecutorBbsMixin._state_machine_bindings(gf)
        assert bindings == {
            "writer": {"source": "auto", "bot_ids": ["w-bot"]},
            "editor": {"source": "manual", "bot_ids": ["e-bot"]},
        }

    def test_state_machine_bindings_rejects_missing_bot_ids(self):
        gf = GroupFormation(bot_ids=["a"], collab_mode="state_machine",
                            extend_props={"participant_bindings": {
                                "writer": {"bot_ids": []},
                                "editor": {"bot_ids": 42},
                            }})
        with pytest.raises(BotIdentityResolutionError, match="must contain bot_ids"):
            TaskExecutorBbsMixin._state_machine_bindings(gf)

    def test_state_machine_bindings_from_dirty_members_info(self):
        gf = GroupFormation(
            bot_ids=["a"], collab_mode="state_machine",
            members_info=["junk", {"role": "writer"}, {"bot_id": "b"},
                          {"bot_id": "b1", "role": "writer"}],
            extend_props={},
        )
        bindings = TaskExecutorBbsMixin._state_machine_bindings(gf)
        assert bindings == {"writer": {"source": "manual", "bot_ids": ["b1"]}}


# ============================================================================
# modal_executor/task_executor_relay.py
# ============================================================================

class _ExplodingOpenApiBot:
    async def send_message(self, **kw):
        raise OpenApiError("send rejected")


class TestResumeRelayTurn:
    def test_missing_holder_waits_for_recovery(self):
        exe = _executor(bot=_SimpleBot(), graph=None)
        node = _node("c1", "t1", run_mode="single_bot", assignee=None)  # 无 holder 无 assignee
        assert _run(exe.resume_relay_turn(node, "turn-x")) is False

    def test_missing_bot_port_waits_for_recovery(self):
        exe = _executor(bot=None)
        node = _node("c1", "t1", extend_props={"relay_holder_id": "bot-h"})
        assert _run(exe.resume_relay_turn(node, "turn-x")) is False

    def test_delivery_failure_returns_false_without_raising(self):
        exe = _executor(bot=_ExplodingOpenApiBot())
        node = _node("c1", "t1", extend_props={"relay_holder_id": "bot-h"})
        assert _run(exe.resume_relay_turn(node, "turn-x")) is False


class TestDispatchBbsRelay:
    def test_relay_mode_dispatch_scopes_target_node(self):
        # full relay dispatch: config orchestration relay → notify 收 target_node_id
        graph = _BbsGraph(dashboard=_dash())  # dashboard 无该节点 → _dispatch_bbs 判 node missing
        exe = _executor(bot=_BbsBot({}), graph=graph, api_base_url="http://x")
        node = _node("ghost", "t1", run_mode="bbs")
        assert _run(exe._dispatch_bbs(node, asyncio.Semaphore(1))) is False

    def test_relay_dispatch_notifies_with_target(self):
        node = _node("rn1", "t1", run_mode="bbs", status=Status.RUNNING)
        dash_graph = _execution_graph("t1")
        dash_graph.extend_props = {"execution_config": {"orchestration_mode": "relay"}}
        dash_graph.tasks.append(node)
        bot = _BbsBot({})
        exe = _executor(bot=bot, graph=_BbsGraph(dashboard=dash_graph),
                        bcn=_RosterBcn(_roster("A")), api_base_url="http://x")
        assert _run(exe._dispatch_bbs(node, asyncio.Semaphore(1))) is True
        # 空竞标(无有效 bid) → 通知完成但无派发
        assert bot.sent == []


# ============================================================================
# modal_executor/task_executor_result_poller.py
# ============================================================================

class _PollerBcs:
    def __init__(self, group, run):
        self._group = group
        self._run = run

    async def get_group(self, group_id):
        return self._group

    async def get_state_machine_run(self, run_id):
        return self._run


class _PollerBot:
    def __init__(self, cancel_exc=None):
        self.cancel_exc = cancel_exc
        self.cancel_calls = []

    async def cancel_run(self, run_id):
        self.cancel_calls.append(run_id)
        if self.cancel_exc is not None:
            raise self.cancel_exc


class TestResultPoller:
    def _poller(self, bot, bcs):
        return TaskExecutorResultPoller(bot=bot, bcs=bcs)

    @pytest.mark.asyncio
    async def test_run_mode_non_terminal_returns_none(self):
        poller = self._poller(None, _PollerBcs({}, {"status": "running"}))
        handle = BcsGroupHandle(loop_task_id="t::n", group_id="g",
                                collab_mode="state_machine", registered_at=0.0, run_id="r1")
        assert await poller._poll_terminal(handle) is None

    @pytest.mark.asyncio
    async def test_session_mode_non_terminal_returns_none(self):
        poller = self._poller(None, _PollerBcs({"session": {"status": "active"}}, {}))
        handle = BcsGroupHandle(loop_task_id="t::n", group_id="g",
                                collab_mode="chat", registered_at=0.0,
                                session_id="s1", run_id=None)
        assert await poller._poll_terminal(handle) is None

    @pytest.mark.asyncio
    async def test_unknown_handle_type_returns_none(self):
        poller = self._poller(None, _PollerBcs({}, {}))
        assert await poller._poll_terminal(object()) is None

    @pytest.mark.asyncio
    async def test_cancel_handle_ignores_group_handles(self):
        poller = self._poller(_PollerBot(), _PollerBcs({}, {}))
        handle = BcsGroupHandle(loop_task_id="t::n", group_id="g",
                                collab_mode="chat", registered_at=0.0)
        assert await poller._cancel_handle(handle) is None  # 仅 single_bot 需取消

    @pytest.mark.asyncio
    async def test_cancel_handle_swallows_port_failure(self):
        poller = self._poller(_PollerBot(cancel_exc=RuntimeError("cancel denied")), _PollerBcs({}, {}))
        handle = SingleBotHandle(loop_task_id="t::n", run_id="r9", bot_id="b",
                                 registered_at=0.0)
        assert await poller._cancel_handle(handle) is None  # 取消是 best-effort,不吞主回投