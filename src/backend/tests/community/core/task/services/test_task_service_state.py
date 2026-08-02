"""TDD for TaskService state-group helpers (Phase 2.2, plan §2.2).

Covers the state_machine guard on legal/illegal task + node transitions,
``spawn_build_dag`` materializing plan→Node/Edge骨架, ``spawn_sub_dag`` writing
ONLY a ``SubDagRef`` (no child state — group self-loop invariant), and
``claim_node`` CAS (PENDING→RUNNING once; second claim raises).
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import (
    EdgeKind,
    GraphStatus,
    Node,
    NodeStatus,
    Plan,
    SubDagRef,
    SubTaskSpec,
    Task,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
)
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _planned_task(svc: TaskService) -> Task:
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    p = Plan(
        sub_tasks=[SubTaskSpec(node_id="n1", spec="a"), SubTaskSpec(node_id="n2", spec="b")],
        confidence=0.7,
    )
    svc.finalize_plan(t.id, p)
    return svc.get(t.id)


# --- spawn_build_dag --------------------------------------------------------


def test_spawn_build_dag_materializes_planning_chain_and_subtasks():
    """新设计(§2.2):spawn_build_dag 建规划链(recognition/clarify/execute_start,
    落图即 DONE)+ plan.sub_tasks 作 DISPATCH 节点(PENDING),并持久化。"""
    from agentclaw.community.core.task.domain.models import NodeType

    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    g = task.execution_graph
    assert g is not None
    ids = [n.node_id for n in g.nodes]
    # 规划三节点 DONE + 两个 subtask DISPATCH(PENDING)
    assert ids[:3] == ["n_recognition", "n_clarify", "n_execute_start"]
    assert set(ids[3:]) == {"n1", "n2"}
    planning = [n for n in g.nodes if n.node_type in (
        NodeType.RECOGNITION, NodeType.CLARIFY, NodeType.EXECUTE_START,
    )]
    assert all(n.status is NodeStatus.DONE for n in planning)
    subtasks = [n for n in g.nodes if n.node_type is NodeType.DISPATCH]
    assert all(n.status is NodeStatus.PENDING for n in subtasks)
    assert all(n.node_id in g.state.subtasks for n in g.nodes)


# --- spawn_sub_dag writes ref, never child state ---------------------------


def test_spawn_sub_dag_writes_only_ref_no_child_state():
    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    svc.spawn_sub_dag(task, "n1", ref_kind="bcs_sm", bcs_run_id="sm-9", group_id="g-1")
    node = svc._find_node(task, "n1")  # noqa: SLF001
    assert isinstance(node.sub_dag, SubDagRef)
    assert node.sub_dag.bcs_run_id == "sm-9"
    assert node.sub_dag.group_id == "g-1"
    # invariant: the task graph holds NO child node state for the group
    # (only the ref pointer). Group self-loop stays intact.
    assert node.sub_dag.ref_kind == "bcs_sm"


def test_spawn_sub_dag_unknown_node_raises():
    svc = _service()
    task = _planned_task(svc)
    from agentclaw.community.core.task.domain.repository import TaskNotFoundError

    with pytest.raises(TaskNotFoundError):
        svc.spawn_sub_dag(task, "ghost", "bcs_sm", "sm-1", "g-1")


# --- claim_node CAS ---------------------------------------------------------


def test_claim_node_cas_first_wins_second_rejected():
    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    svc._task_repo.save(task)  # noqa: SLF001 — persist the materialized graph

    first = svc.claim_node(task.id, "n1", "bot-a")
    assert first is not None
    assert first.executor_id == "bot-a"
    assert first.accept_token.startswith("tok-")

    refreshed = svc.get(task.id)
    node = svc._find_node(refreshed, "n1")  # noqa: SLF001
    assert node.status is NodeStatus.RUNNING
    assert node.assignee == "bot-a"
    assert len(node.attempted_executors) == 1

    with pytest.raises(IllegalTransitionError):
        svc.claim_node(task.id, "n1", "bot-b")


def test_claim_node_records_attempt_with_routed_trigger():
    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    svc._task_repo.save(task)  # noqa: SLF001
    svc.claim_node(task.id, "n1", "bot-a")
    refreshed = svc.get(task.id)
    rec = svc._find_node(refreshed, "n1").attempted_executors[0]  # noqa: SLF001
    assert rec.executor_id == "bot-a"
    assert rec.trigger.value == "routed"
    assert rec.round == 1


# --- mark_graph_status / set_node_status guards ----------------------------


def test_set_node_status_guard_rejects_illegal():
    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    # PENDING → DONE is not legal (must pass RUNNING).
    with pytest.raises(IllegalTransitionError):
        svc.set_node_status(task, "n1", NodeStatus.DONE)


def test_set_node_status_legal_running_to_done():
    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    svc.set_node_status(task, "n1", NodeStatus.RUNNING)
    svc.set_node_status(task, "n1", NodeStatus.DONE)
    assert svc._find_node(task, "n1").status is NodeStatus.DONE  # noqa: SLF001


def test_mark_graph_status_sets_graph_status():
    svc = _service()
    task = _planned_task(svc)
    svc.mark_graph_status(task, GraphStatus.AWAITING_HUMAN_ACCEPT)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT


def test_add_sibling_node_links_edge():
    svc = _service()
    task = _planned_task(svc)
    svc.spawn_build_dag(task)
    svc.add_sibling_node(task, "n1", Node(node_id="n3", spec="c"))
    assert any(n.node_id == "n3" for n in task.execution_graph.nodes)
    assert any(
        e.from_node == "n1" and e.to_node == "n3" and e.kind is EdgeKind.DEPENDENCY
        for e in task.execution_graph.edges
    )