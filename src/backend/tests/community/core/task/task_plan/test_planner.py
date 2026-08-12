"""M3 TaskPlanner 单测(对齐 tasks.md T3.x)。

in-test StubDecomposer 注入;真实 TaskGraphService 构造图场景。
覆盖:触发条件(根PENDING/PLANNING/FAILED+gaps叶/无目标)、去重、decompose[]→plan[]、换 stub、零 case。
"""
from __future__ import annotations

from typing import Callable

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_plan.planner import TaskPlanner


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


def _child(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec, run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


class StubDecomposer:
    def __init__(self, factory: Callable[[object], list[TaskNode]] | None = None):
        self._factory = factory or (lambda g: [])
        self.decompose_calls = 0

    def decompose(self, graph) -> list[TaskNode]:
        self.decompose_calls += 1
        return self._factory(graph)


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


@pytest.fixture
def graph(svc):
    return svc.initialize_graph(_task_info())


class TestPlanTrigger:
    def test_root_pending_initial_target(self, svc, graph):
        # 根 PENDING(无父)→ 初始规划目标
        planner = TaskPlanner(StubDecomposer(lambda g: [_child("c1"), _child("c2")]))
        result = planner.plan(svc.query_task_dashboard("t1"))
        assert {n.node_id for n in result} == {"c1", "c2"}

    def test_planning_parent_target(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")  # 根进 PLANNING
        planner = TaskPlanner(StubDecomposer(lambda g: [_child("c1a")]))
        result = planner.plan(svc.query_task_dashboard("t1"))
        assert [n.node_id for n in result] == ["c1a"]

    def test_failed_gaps_leaf_target(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["缺x"])))
        planner = TaskPlanner(StubDecomposer(lambda g: [_child("c1_remedy")]))
        result = planner.plan(svc.query_task_dashboard("t1"))
        assert [n.node_id for n in result] == ["c1_remedy"]

    def test_no_target_when_root_running(self, svc):
        # 根 RUNNING(非 PENDING/PLANNING/FAILED)→ 无目标,不调 decompose
        svc.initialize_graph(_task_info("tX"))
        svc.update_task_node_info(_patch("tX", "tX", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        stub = StubDecomposer(lambda g: [_child("x")])
        planner = TaskPlanner(stub)
        result = planner.plan(svc.query_task_dashboard("tX"))
        assert result == []
        assert stub.decompose_calls == 0


class TestPlanDedup:
    def test_dedup_existing(self, svc, graph):
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")  # c1 已存
        planner = TaskPlanner(StubDecomposer(lambda g: [_child("c1"), _child("c2")]))
        result = planner.plan(svc.query_task_dashboard("t1"))
        assert [n.node_id for n in result] == ["c2"]  # 去重 c1


class TestDecomposeEmpty:
    def test_decompose_empty_returns_empty(self, svc, graph):
        planner = TaskPlanner(StubDecomposer(lambda g: []))
        result = planner.plan(svc.query_task_dashboard("t1"))
        assert result == []


class TestSwapStub:
    def test_swap_stub_changes_output(self, svc, graph):
        p1 = TaskPlanner(StubDecomposer(lambda g: [_child("dim_a")]))
        p2 = TaskPlanner(StubDecomposer(lambda g: [_child("dim_b"), _child("dim_c")]))
        r1 = p1.plan(svc.query_task_dashboard("t1"))
        r2 = p2.plan(svc.query_task_dashboard("t1"))
        assert {n.node_id for n in r1} == {"dim_a"}
        assert {n.node_id for n in r2} == {"dim_b", "dim_c"}


class TestZeroCase:
    def test_no_node_name_literals(self):
        import agentclaw.community.core.task.task_plan.planner as m
        src = open(m.__file__).read()
        forbidden = ["N_overview", "N_market", "N_aggregate", "N_verify", "N_report", "N_practice", "n_root", "dim_"]
        hits = [f for f in forbidden if f in src]
        assert hits == [], f"planner 出现写死节点名: {hits}"
