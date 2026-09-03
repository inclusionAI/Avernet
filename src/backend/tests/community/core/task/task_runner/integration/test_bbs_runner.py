# tests/community/core/task/task_runner/integration/test_bbs_runner.py
import asyncio
import json
from unittest.mock import MagicMock

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status,
    TaskExecutionGraph, TaskNode, TaskNodePatch, TaskSpec,
)
from agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor import (
    _bid_prompt, _parse_bid, notify,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _execution_graph(task_id="t1", objective="整理基础架构方向架构师名册"):
    """真实最小 TaskExecutionGraph:一个根 TaskNode(BBS 升态)。

    bid prompt 现内联 task snapshot,需真实图(MagicMock 会让 json.dumps 失败);根 node_id == task_id。
    """
    root = TaskNode(
        node_id=task_id, task_id=task_id, status=Status.HUNG,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="架构师名册", instruction="整理3位架构师"),
            context=Context(background="基础架构方向"),
            goal=Goal(objective=objective,
                      acceptances=[AcceptanceCriteria("ac_arch", "给出3位架构师姓名/角色+职责")]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )
    return TaskExecutionGraph(run_id=1, loop_round=2, status=Status.HUNG, tasks=[root], task_id=task_id)


class _FakeBot:
    """bid 与 dispatch 共用 send_and_wait_async(bid prompt 含 "[bbs-bid]" 标记,dispatch msg 为任务快照)。

    ``rates``: {bot_id: completion_rate or None (None=simulate bid error)}。
    ``dispatch_raises``: 若为真,dispatch 抛异常 → 走 notify except 收口分支(释放 claim)。
    """

    def __init__(self, rates, *, dispatch_raises: bool = False, reasons=None, titles=None, goals=None):
        self._rates = rates
        self._reasons = reasons or {}
        self._titles = titles or {}
        self._goals = goals or {}
        self.dispatch_raises = dispatch_raises
        self.sent_messages: list[tuple] = []   # dispatch 消息(给胜出 bot)
        self.bid_prompts: list[str] = []        # bid prompt

    async def send_and_wait_async(self, *, bot_id, message, metadata=None, timeout=180.0, poll_interval=2.0):
        if "[bbs-bid]" in message:
            # Phase 1: bid(并发评估)→ 回 completion_rate(bid prompt 不会被记入 sent_messages)
            self.bid_prompts.append(message)
            rate = self._rates.get(bot_id)
            if rate is None:
                raise RuntimeError("bot error")
            reason = self._reasons.get(bot_id, f"reason-{bot_id}")
            bid_obj: dict = {"completion_rate": rate, "relay_reason": reason}
            title = self._titles.get(bot_id, "")
            goal = self._goals.get(bot_id, "")
            if title:
                bid_obj["title"] = title
            if goal:
                bid_obj["goal"] = goal
            return {"status": "COMPLETED",
                    "result": {"content": json.dumps(bid_obj, ensure_ascii=False)}}
        # Phase 2: dispatch(给胜出 bot 发任务,等结果)
        self.sent_messages.append((bot_id, message, metadata))
        if self.dispatch_raises:
            raise RuntimeError("dispatch failed")
        return {
            "status": "COMPLETED",
            "result": {"content": "dispatched-task-result"},
            "session_id": "sess-dispatch",
        }

    async def send_message(self, *, bot_id, message, metadata):
        # 兼容保留:notify 不再调用 send_message(dispatch 走 send_and_wait_async)
        self.sent_messages.append((bot_id, message, metadata))
        from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
        return BotSendResult(run_id=f"r_{bot_id}", session_id=None)


class _FakeBcn:
    """Fake BcnService.list_bots_by_task_modes: sync (bbs_runner 经 asyncio.to_thread 调),记录断言。"""

    def __init__(self, roster):
        self._roster = roster

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any"):
        assert claim is True
        assert dream is None
        assert match == "all"
        return list(self._roster)



class _FlakyBcn(_FakeBcn):
    def __init__(self, roster, failures=1):
        super().__init__(roster)
        self.failures = failures
        self.calls = 0

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any"):
        self.calls += 1
        assert claim is True
        assert dream is None
        assert match == "all"
        if self.calls <= self.failures:
            raise RuntimeError("roster unavailable")
        return list(self._roster)



def _roster(*bot_ids: str) -> list[dict]:
    return [
        {
            "bot_id": bot_id,
            "name": bot_id,
            "env": "local",
            "task_claim_mode": True,
            "task_dream_mode": True,
        }
        for bot_id in bot_ids
    ]


class _FakeGraph:
    """轻量 graph 替身:记账 claim/release/bbs_owner 当前值,坚持 scoped 节点新增(不真实落图)。"""

    def __init__(self):
        self.claimed: str | None = None     # 历史 claim 的 winner(不重置,供断言)
        self.bbs_owner: str | None = None   # 当前根 bbs_owner 值(claim 设,收口/except 清)
        self.cleared = False                 # bbs_owner 被清回 None 标记(收口 finally / except 释放)
        self.added_nodes = []                # notify 创建的 scoped BBS 节点
        self.root_status = Status.HUNG       # BBS 创建前根节点的恢复态

    def claim_bbs_owner(self, task_id, bot_id):
        self.claimed = bot_id
        self.bbs_owner = bot_id
        return MagicMock(success=True)

    def update_task_node_info(self, patch):
        if (
            patch.extend_props_patch
            and "bbs_owner" in patch.extend_props_patch
            and patch.extend_props_patch["bbs_owner"] is None
        ):
            self.bbs_owner = None
            self.cleared = True

    def add_task_nodes(self, nodes, task_id, *, mark_parent_planning=True):
        # Mirror TaskGraphService's parent-state side effect so this test catches
        # accidental HUNG -> PLANNING transitions in the BBS runner.
        self.added_nodes.extend(nodes)
        if mark_parent_planning:
            self.root_status = Status.PLANNING
        return MagicMock(success=True)

    def add_relations(self, task_id, edges):
        return MagicMock(success=True)


class _FakeOnBbsReport:
    """模拟 engine.on_bbs_report:持有者校验 → 翻 scoped 节点 → finally 释放 claim(bbs_owner=None)。

    与真实 engine.on_bbs_report 对齐:bbs_owner 须 == patch.assignee,否则 TaskStateError
    (持卡者死锁保护 —— 非 claim 持有者不得收口)。这同时也是 notify 收口 patch 必须带 assignee 的回归闸。
    """

    def __init__(self, graph):
        self.graph = graph
        self.calls: list[TaskNodePatch] = []

    async def __call__(self, patch):
        self.calls.append(patch)
        if patch.assignee is None or patch.assignee != self.graph.bbs_owner:
            raise TaskStateError(
                f"on_bbs_report: 非claim持有者 task={patch.task_id}"
            )
        try:
            self.graph.update_task_node_info(patch)
        finally:
            self.graph.update_task_node_info(
                TaskNodePatch(task_id=patch.task_id, node_id=patch.task_id,
                              extend_props_patch={"bbs_owner": None})
            )


_GOAL = "整理基础架构方向架构师名册"


def test_notify_keeps_hung_root_until_bbs_report():
    """Creating the BBS scoped node must not resume root planning early."""
    graph = _FakeGraph()
    bot = _FakeBot(rates={"A": 80})
    bcn = _FakeBcn(_roster("A"))

    _run(notify(
        _execution_graph("t-hung-root"),
        bcn=bcn,
        bot=bot,
        graph=graph,
        backend_url="http://x",
        on_bbs_report=_FakeOnBbsReport(graph),
    ))

    assert graph.root_status == Status.HUNG
    assert len(graph.added_nodes) == 1


def test_notify_selects_highest_completion_rate_and_claims_and_sends():
    """bid→select→claim→dispatch→收口走引擎:选最高 completion_rate 的 bot、claim 根,经 on_bbs_report 收口
    (翻 scoped SUCCESS + finally 释放 claim)而非裸写根/ scoped 节点。"""
    roster = _roster("A", "B", "C")
    bot = _FakeBot(rates={"A": 50, "B": 90, "C": 70})
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    g = _execution_graph("t1", _GOAL)
    on_bbs_report = _FakeOnBbsReport(graph)

    _run(notify(g, bcn=bcn, bot=bot, graph=graph, backend_url="http://localhost:8888",
                skill_name="bbs-relay-single-task", on_bbs_report=on_bbs_report))

    assert graph.claimed == "B"  # 最高 completion_rate 的 bot 胜出
    assert len(bot.sent_messages) == 1           # 只给胜出 bot dispatch 一次
    msg_bot, msg_text, msg_meta = bot.sent_messages[0]
    assert msg_bot == "B"
    # notify 仅完成 BBS 投递与 scoped 节点回写；根节点仍保持 HUNG，
    # 后续由 callback/report 进入 engine.on_bbs_report 才能统一收口。
    assert on_bbs_report.calls == []
    assert graph.bbs_owner == "B"
    assert not graph.cleared
    assert len(graph.added_nodes) == 1
    scoped = graph.added_nodes[0]
    assert scoped.run_info.start_time is not None
    assert scoped.run_info.extend_props["bbs_claim_at"] == scoped.run_info.start_time
    # bid prompt + dispatch msg 均内联任务态快照(goal objective 嵌入),而非只发 task_id
    assert bot.bid_prompts, "bid 未发出(空 bid_prompts)"
    assert any(_GOAL in p for p in bot.bid_prompts), "bid prompt 未内联 goal snapshot"
    assert _GOAL in msg_text, "dispatch msg 未内联 snapshot"


def test_notify_empty_roster_returns_silently():
    """空 roster → 静默返回(不 claim、不 send)。"""
    bot = _FakeBot(rates={})
    bcn = _FakeBcn([])
    graph = _FakeGraph()
    _run(notify(_execution_graph("t2"), bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s"))
    assert graph.claimed is None
    assert bot.sent_messages == []
    assert bot.bid_prompts == []


def test_notify_retries_roster_failure_and_only_filters_claim(monkeypatch):
    import agentclaw.community.core.task.task_runner.modal_executor.bbs_modal_executor as module

    monkeypatch.setattr(module, "_ROSTER_RETRY_DELAY", 0)
    bcn = _FlakyBcn(_roster("A"), failures=1)
    bot = _FakeBot(rates={"A": 80})
    graph = _FakeGraph()

    _run(notify(_execution_graph("t-retry"), bcn=bcn, bot=bot, graph=graph, backend_url="http://x"))

    assert bcn.calls == 2
    assert graph.claimed == "A"
    assert len(bot.sent_messages) == 1


def test_notify_all_bids_failed_returns_silently():
    """全 bid 失败/超时 → 静默返回。"""
    roster = _roster("A")
    bot = _FakeBot(rates={"A": None})  # None → raises
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    _run(notify(_execution_graph("t3"), bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s"))
    assert graph.claimed is None


def test_notify_send_message_failure_rolls_back_claim():
    """dispatch (send_and_wait_async) 失败 → 走 except 收口:释放 claim(bbs_owner=None),不调 on_bbs_report。

    覆盖 except 分支的声明释放逻辑:claim 已置根 bbs_owner=W,dispatch 抛错 → except 写 bbs_owner=None
    回收声明,避免 claim 泄漏挡住后续重升 BBS;收口 on_bbs_report 不触发(dispatch 未产出)。
    """
    roster = _roster("W")
    bot = _FakeBot(rates={"W": 80}, dispatch_raises=True)  # bid 成功→选 W→claim→dispatch 抛错
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    on_bbs_report = _FakeOnBbsReport(graph)
    _run(notify(_execution_graph("t4"), bcn=bcn, bot=bot, graph=graph,
                backend_url="http://x", skill_name="s", on_bbs_report=on_bbs_report))
    assert graph.claimed == "W"      # claim 确已发生(claim 在 dispatch 之前)
    assert graph.bbs_owner is None   # except 释放后根 bbs_owner 回 None
    assert graph.cleared             # bbs_owner 被清(声明释放)
    assert on_bbs_report.calls == [] # dispatch 失败,未走收口


def test_notify_bcn_none_returns_silently():
    _run(notify(_execution_graph("t5"), bcn=None, bot=_FakeBot({}), graph=_FakeGraph(), backend_url="http://x"))
    # no exception, no claim


class _RecordingGraph(_FakeGraph):
    """记录全部 update_task_node_info patch(供无回回归路径断言 root 写入范围)。"""

    def __init__(self):
        super().__init__()
        self.patches: list[TaskNodePatch] = []

    def update_task_node_info(self, patch):
        self.patches.append(patch)
        super().update_task_node_info(patch)


def test_notify_without_callback_keeps_root_untouched_and_records_scoped_output():
    """缺少 callback/report 收口时，BBS notify 只能写 scoped 节点，根保持 HUNG 和 claim。

    这避免执行器在 BBS lease 尚未被 callback 确认时提前释放 ``bbs_owner``、
    改写根 output 或把根节点重新推进为 PLANNING/EXECUTING。
    """
    roster = _roster("W")
    bot = _FakeBot(rates={"W": 80})
    bcn = _FakeBcn(roster)
    graph = _RecordingGraph()
    g = _execution_graph("t6", _GOAL)

    _run(notify(g, bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s"))  # on_bbs_report 缺省 None

    root_patches = [p for p in graph.patches if p.node_id == "t6"]
    assert root_patches == []
    assert graph.bbs_owner == "W"
    assert not graph.cleared
    # scoped 接力节点仍落自身执行产出(属 runner 执行完成回投,非根 output 污染)
    scoped = [p for p in graph.patches if p.node_id != "t6"]
    # _bbs_output = task_result["result"] = {"content": "dispatched-task-result"}(FakeBot dispatch 返回结构)
    assert len(scoped) == 1 and scoped[0].output_patch == {"output": {"content": "dispatched-task-result"}}
    assert graph.claimed == "W"  # bid→select→claim 链路完整


def test_bid_prompt_asks_for_relay_reason():
    """bid prompt 除 completion_rate 外,还要求 bot 输出 relay_reason(为什么觉得自己能完成 + 依据)。"""
    prompt = _bid_prompt(_execution_graph("t1", _GOAL), "B")
    assert "completion_rate" in prompt
    assert "relay_reason" in prompt


def test_parse_bid_extracts_relay_reason():
    """_parse_bid 从 bot 回复 JSON 解析 completion_rate + relay_reason。"""
    bid = _parse_bid({
        "bot_id": "B",
        "run": {"status": "COMPLETED",
                "result": {"content": json.dumps({"completion_rate": 90, "relay_reason": "已有相关产出,可补完剩余 gap"})}},
    })
    assert bid == {"bot_id": "B", "completion_rate": 90, "relay_reason": "已有相关产出,可补完剩余 gap",
                   "title": "", "goal": ""}


def test_parse_bid_relay_reason_defaults_empty_when_missing():
    """bot 未输出 relay_reason → 默认空串(bid 仍有效,只要 completion_rate>0;选优键仍是完成率)。"""
    bid = _parse_bid({
        "bot_id": "A",
        "run": {"status": "COMPLETED",
                "result": {"content": json.dumps({"completion_rate": 50})}},
    })
    assert bid == {"bot_id": "A", "completion_rate": 50, "relay_reason": "", "title": "", "goal": ""}


def test_notify_records_winner_relay_reason_in_scoped_extend_props():
    """胜出 bot 的 relay_reason 落到 scoped bbs 接力节点 run_info.extend_props(与 assignee_bot_id/output/session_id 同处)。"""
    roster = _roster("A", "B", "C")
    bot = _FakeBot(
        rates={"A": 50, "B": 90, "C": 70},
        reasons={"A": "A 可做", "B": "已产出相关交付,可补完剩余 gap", "C": "C 可做"},
    )
    bcn = _FakeBcn(roster)
    graph = _RecordingGraph()
    g = _execution_graph("t1", _GOAL)
    on_bbs_report = _FakeOnBbsReport(graph)

    _run(notify(g, bcn=bcn, bot=bot, graph=graph, backend_url="http://localhost:8888",
                skill_name="bbs-relay-single-task", on_bbs_report=on_bbs_report))

    assert graph.claimed == "B"  # 最高 completion_rate 胜出(选优键未变)
    assert on_bbs_report.calls == []
    scoped = [p for p in graph.patches if p.node_id != "t1"]
    assert len(scoped) == 1
    assert scoped[0].extend_props_patch["relay_reason"] == "已产出相关交付,可补完剩余 gap"


def test_bid_prompt_asks_for_title_and_goal_of_completable_part():
    """bid prompt 除 completion_rate/relay_reason 外,还要求 bot 输出它能完成的那部分事项的 title 与 goal。"""
    prompt = _bid_prompt(_execution_graph("t1", _GOAL), "B")
    assert "completion_rate" in prompt
    assert "relay_reason" in prompt
    assert "title" in prompt
    assert "goal" in prompt


def test_parse_bid_extracts_title_and_goal():
    """_parse_bid 从 bot 回复 JSON 解析它能完成的这部分事项的 title + goal。"""
    bid = _parse_bid({
        "bot_id": "B",
        "run": {"status": "COMPLETED",
                "result": {"content": json.dumps({
                    "completion_rate": 90,
                    "relay_reason": "已产出相关交付,可补完剩余 gap",
                    "title": "补完架构师名册剩余 2 位",
                    "goal": "给出剩余 2 位架构师姓名/角色/职责",
                }, ensure_ascii=False)}},
    })
    assert bid == {
        "bot_id": "B",
        "completion_rate": 90,
        "relay_reason": "已产出相关交付,可补完剩余 gap",
        "title": "补完架构师名册剩余 2 位",
        "goal": "给出剩余 2 位架构师姓名/角色/职责",
    }


def test_notify_brings_winner_title_goal_into_execution_message():
    """胜出 bot 的 bid title/goal(它能完成的这部分事项)带入真正执行的任务消息(dispatch msg)。

    非胜出 bot 的 title/goal 不得泄进执行消息。"""
    roster = _roster("A", "B")
    bot = _FakeBot(
        rates={"A": 50, "B": 90},
        reasons={"A": "A 理由", "B": "B 理由"},
        titles={"A": "A 部分标题", "B": "B 部分标题"},
        goals={"A": "A 部分目标", "B": "B 部分目标"},
    )
    bcn = _FakeBcn(roster)
    graph = _FakeGraph()
    g = _execution_graph("t1", _GOAL)
    on_bbs_report = _FakeOnBbsReport(graph)

    _run(notify(g, bcn=bcn, bot=bot, graph=graph, backend_url="http://x", skill_name="s",
                on_bbs_report=on_bbs_report))

    assert graph.claimed == "B"  # 最高 completion_rate 胜出
    assert len(bot.sent_messages) == 1
    msg_bot, msg_text, msg_meta = bot.sent_messages[0]
    assert msg_bot == "B"
    assert "B 部分标题" in msg_text  # 胜出 bot 的 bid title 进执行消息
    assert "B 部分目标" in msg_text  # 胜出 bot 的 bid goal 进执行消息
    assert "A 部分" not in msg_text  # 未胜出 bot 的 title/goal 不进执行消息
