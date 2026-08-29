"""M3 TaskPlanner 单测(对齐 tasks.md T3.x)。

in-test 策略注入(包 StubDecomposer 成 PlanningStrategy adapter);真实 TaskGraphService 构图。
覆盖:触发条件(根PENDING/PLANNING/FAILED+gaps叶/无目标)、去重、decompose[]→plan[]、换策略、零 case。
零参 TaskPlanner(graph);corp 注入策略经 set_strategies(测试模拟 corp 最简形态)。
"""
from __future__ import annotations

import asyncio
from typing import Callable

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_plan.planner import TaskPlanner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


class _StubPlanningStrategy:
    """包旧 StubDecomposer(decompose(graph)) 成 PlanningStrategy adapter(测试模拟 corp 策略注入)。"""

    rule_id = "stub"
    priority = 5

    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None):
        self._factory = factory or (lambda g: [])
        self.decompose_calls = 0

    async def matches(self, graph) -> bool:
        return True  # 兜底(高于内置 gap/workflow)

    async def apply(self, graph, target) -> PlanResult:
        self.decompose_calls += 1
        kids = self._factory(graph)
        return PlanResult(children=kids, has_gap=bool(kids))


def _planner(svc, factory=None) -> TaskPlanner:
    p = TaskPlanner(svc)
    p.set_strategies([_StubPlanningStrategy(factory)])
    return p


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


@pytest.fixture
def graph(svc):
    return svc.initialize_graph(_task_info())


class TestPlanTrigger:
    def test_root_pending_initial_target(self, svc, graph):
        planner = _planner(svc, lambda g: [_child("c1"), _child("c2")])
        result = _run(planner.plan(svc.query_task_dashboard("t1")))
        assert {n.node_id for n in result.children} == {"c1", "c2"}

    def test_planning_parent_target(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        planner = _planner(svc, lambda g: [_child("c1a")])
        result = _run(planner.plan(svc.query_task_dashboard("t1"), target_node_id="t1"))
        assert [n.node_id for n in result.children] == ["c1a"]

    def test_failed_gaps_leaf_target(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"])))
        planner = _planner(svc, lambda g: [_child("c1_remedy")])
        result = _run(planner.plan(svc.query_task_dashboard("t1"), target_node_id="c1"))
        assert [n.node_id for n in result.children] == ["c1_remedy"]

    def test_no_target_when_root_running(self, svc):
        svc.initialize_graph(_task_info("tX"))
        svc.update_task_node_info(_patch("tX", "tX", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        stub = _StubPlanningStrategy(lambda g: [_child("x")])
        planner = TaskPlanner(svc)
        planner.set_strategies([stub])
        result = _run(planner.plan(svc.query_task_dashboard("tX")))
        assert result.children == []
        assert stub.decompose_calls == 0


class TestPlanDedup:
    def test_dedup_existing(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        planner = _planner(svc, lambda g: [_child("c1"), _child("c2")])
        result = _run(planner.plan(svc.query_task_dashboard("t1"), target_node_id="t1"))
        assert [n.node_id for n in result.children] == ["c2"]


class TestDecomposeEmpty:
    def test_decompose_empty_returns_empty(self, svc, graph):
        planner = _planner(svc, lambda g: [])
        result = _run(planner.plan(svc.query_task_dashboard("t1")))
        assert result.children == []


class TestSwapStub:
    def test_swap_stub_changes_output(self, svc, graph):
        p1 = _planner(svc, lambda g: [_child("dim_a")])
        p2 = _planner(svc, lambda g: [_child("dim_b"), _child("dim_c")])
        r1 = _run(p1.plan(svc.query_task_dashboard("t1")))
        r2 = _run(p2.plan(svc.query_task_dashboard("t1")))
        assert {n.node_id for n in r1.children} == {"dim_a"}
        assert {n.node_id for n in r2.children} == {"dim_b", "dim_c"}


class TestZeroCase:
    def test_no_node_name_literals(self):
        import agentclaw.community.core.task.task_plan.planner as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"planner 出现写死节点名: {hits}"
