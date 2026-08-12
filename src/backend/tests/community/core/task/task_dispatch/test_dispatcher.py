"""M4a TaskDispatcher 单测(对齐 tasks.md T4a.x)。

in-test StubBotDiscover/StubRunner 注入。覆盖:四态填 TaskNode.run_info、MISS 标 miss_events、
BBS 退化、不写图不起 run(dispatcher 不调 graph)。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_dispatch.protocols import (
    GroupFormation,
    SearchOutcome,
    SearchResult,
)


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
    )


def _node(node_id: str = "c1", run_mode: str | None = None, assignee: str | None = None) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id="t1", status=Status.PENDING,
        task_spec=_task_info().task_spec,
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
        node_run_graph=None,  # type: ignore[arg-type]
    )


class StubBotDiscover:
    def __init__(self, result: SearchResult):
        self._result = result
        self.search_calls: list[TaskNode] = []

    def search(self, node: TaskNode) -> SearchResult:
        self.search_calls.append(node)
        return self._result


class StubRunner:
    def __init__(self, gid: str = "grp_dyn"):
        self._gid = gid
        self.form_calls: list[GroupFormation] = []

    def form_coop_group(self, gf: GroupFormation) -> str:
        self.form_calls.append(gf)
        return self._gid


@pytest.fixture
def runner() -> StubRunner:
    return StubRunner()


class TestFourStates:
    def test_hit_single(self, runner):
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot_market"))
        d = TaskDispatcher(discover, runner)
        node = _node("c1")
        out = d.dispatch([node])
        assert out[0].run_info.run_mode == "single_bot"
        assert out[0].run_info.assignee == "bot_market"

    def test_hit_group(self, runner):
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.HIT_GROUP, group_id="grp_tech"))
        d = TaskDispatcher(discover, runner)
        out = d.dispatch([_node("c1")])
        assert out[0].run_info.run_mode == "coop_group"
        assert out[0].run_info.assignee == "grp_tech"

    def test_hit_multi_bots_forms_group(self, runner):
        gf = GroupFormation(bot_ids=["bot_a", "bot_b"], collab_mode="manager_worker")
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.HIT_MULTI_BOTS, group_formation=gf))
        d = TaskDispatcher(discover, runner)
        out = d.dispatch([_node("c1")])
        assert out[0].run_info.run_mode == "coop_group"
        assert out[0].run_info.assignee == "grp_dyn"
        assert len(runner.form_calls) == 1
        assert runner.form_calls[0].bot_ids == ["bot_a", "bot_b"]
        assert runner.form_calls[0].collab_mode == "manager_worker"

    def test_miss_no_assignee_marks_events(self, runner):
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_bot_match"))
        d = TaskDispatcher(discover, runner)
        out = d.dispatch([_node("c1")])
        assert out[0].run_info.run_mode is None
        assert out[0].run_info.assignee is None
        assert out[0].run_info.extend_props.get("miss_events") == ["no_bot_match"]


class TestBbsDegradation:
    def test_bbs_node_skips_search(self, runner):
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.MISS))
        d = TaskDispatcher(discover, runner)
        # BBS 节点:run_mode 已 "bbs",assignee 已标
        node = _node("c1", run_mode="bbs", assignee="bot_bbs")
        out = d.dispatch([node])
        assert out[0].run_info.run_mode == "bbs"  # 维持
        assert out[0].run_info.assignee == "bot_bbs"
        assert len(discover.search_calls) == 0  # 不调搜推


class TestNoWriteGraph:
    def test_dispatch_does_not_touch_graph(self, runner):
        # dispatcher 不持 graph、不写图:验证 dispatch 不调任何 graph 方法
        # (fixture 无 graph;dispatcher 仅持 discover+runner)
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="b1"))
        d = TaskDispatcher(discover, runner)
        out = d.dispatch([_node("c1"), _node("c2")])
        assert len(out) == 2  # 仅填充入参返回,不写图
        # 验证 dispatcher 无 _graph 属性
        assert not hasattr(d, "_graph")


class TestEmpty:
    def test_empty_list(self, runner):
        discover = StubBotDiscover(SearchResult(outcome=SearchOutcome.MISS))
        d = TaskDispatcher(discover, runner)
        assert d.dispatch([]) == []
