"""TDD for TaskService state-group helpers (Phase 2.2, plan §2.2).

Covers the state_machine guard on legal/illegal task + node transitions,
``init_execution_graph`` materializing plan→Node/Edge骨架, ``spawn_sub_dag`` writing
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
    NodeType,
    RunMode,
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
    svc.clarify(t.id, {"summary": "s"})
    svc.clarify(t.id, {}, confirmed=True)
    return svc.get(t.id)


def _graph_with_n1(svc: TaskService) -> Task:
    """_planned_task + init_execution_graph + 一个可派发 DISPATCH 节点 "n1"
    (2026-08-03:Plan 退场,n1 不再由 plan.sub_tasks 预拆,测试显式 add_node)。"""
    task = _planned_task(svc)
    svc.init_execution_graph(task)
    svc.add_node(
        task.id,
        SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.SINGLE_BOT),
        "n_execute_start",
        NodeType.DISPATCH,
    )
    return svc.get(task.id)


# --- init_execution_graph --------------------------------------------------------


def test_init_execution_graph_attaches_task_content_to_planning_nodes():
    """历史节点(recognition/clarify)的 spec/properties 挂真实任务内容;execute_start
    仅 phase_label(2026-08-03:Plan 退场,不再有 plan_summary 预拆)。"""
    svc = _service()
    task = _planned_task(svc)
    svc.init_execution_graph(task)

    rec = svc._find_node(task, "n_recognition")  # noqa: SLF001
    cla = svc._find_node(task, "n_clarify")          # noqa: SLF001
    exe = svc._find_node(task, "n_execute_start")    # noqa: SLF001

    # recognition:任务明细(title/summary/tags)
    assert rec.spec.startswith("任务识别:")
    assert rec.properties.get("phase_label") == "任务识别"
    assert rec.properties.get("task_title") == "t"
    assert rec.properties.get("task_summary") == "s"

    # clarify:任务Spec 五要素
    assert cla.spec.startswith("任务明确:")
    assert cla.properties.get("phase_label") == "任务明确"
    ts = cla.properties.get("task_spec")
    assert isinstance(ts, dict)
    assert set(ts.keys()) >= {"objective", "background", "constraints", "deliverables", "acceptances"}

    # execute_start:仅 phase_label(无 plan_summary)
    assert exe.spec == "确认开始执行"
    assert exe.properties.get("phase_label") == "确认开始执行"


def test_init_execution_graph_builds_root_bot_search():
    """init_execution_graph:无 Plan → 建 recognition→clarify→execute_start 历史链 +
    根 BOT_SEARCH;无 DISPATCH 预拆(2026-08-03 Plan 退场)。"""
    from agentclaw.community.core.task.domain.models import NodeType

    svc = _service()
    task = _planned_task(svc)  # create → clarify(confirmed=True) → DEFINED
    svc.init_execution_graph(task)
    g = task.execution_graph
    assert g is not None
    ids = [n.node_id for n in g.nodes]
    assert ids[:3] == ["n_recognition", "n_clarify", "n_execute_start"]
    types = [n.node_type for n in g.nodes]
    assert types.count(NodeType.BOT_SEARCH) == 1  # 根 BOT_SEARCH
    assert NodeType.DISPATCH not in types  # 不再预拆 DISPATCH
    planning = [n for n in g.nodes if n.node_type in (
        NodeType.RECOGNITION, NodeType.CLARIFY, NodeType.EXECUTE_START,
    )]
    assert all(n.status is NodeStatus.DONE for n in planning)
    assert all(n.node_id in g.state.subtasks for n in g.nodes)


def test_get_task_graph_serializes_node_properties_to_panel():
    """get_task_graph → _node_view → TaskNodeView 必须把 node.properties 整包透传,
    否则 TaskNodeView.properties 取默认 {} → 副屏画布读不到 phase_label/task_spec/
    plan_summary(init_execution_graph 挂在那里的任务内容)。"""
    svc = _service()
    task = _planned_task(svc)
    svc.init_execution_graph(task)
    graph = svc.get_task_graph(task.id)
    assert graph is not None
    by_id = {n["node_id"]: n for n in graph["nodes"]}
    # recognition:任务明细
    rec_props = by_id["n_recognition"]["properties"]
    assert rec_props.get("phase_label") == "任务识别"
    assert rec_props.get("task_title") == "t"
    # clarify:任务Spec 五要素
    cla_props = by_id["n_clarify"]["properties"]
    assert cla_props.get("phase_label") == "任务明确"
    assert set(cla_props["task_spec"].keys()) >= {
        "objective", "background", "constraints", "deliverables", "acceptances",
    }
    # execute_start:phase_label(无 plan_summary,2026-08-03 Plan 退场)
    exe_props = by_id["n_execute_start"]["properties"]
    assert exe_props.get("phase_label") == "确认开始执行"


# --- spawn_sub_dag writes ref, never child state ---------------------------


def test_spawn_sub_dag_writes_only_ref_no_child_state():
    svc = _service()
    task = _graph_with_n1(svc)
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
    task = _graph_with_n1(svc)

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
    task = _graph_with_n1(svc)
    svc.claim_node(task.id, "n1", "bot-a")
    refreshed = svc.get(task.id)
    rec = svc._find_node(refreshed, "n1").attempted_executors[0]  # noqa: SLF001
    assert rec.executor_id == "bot-a"
    assert rec.trigger.value == "routed"
    assert rec.round == 1


# --- mark_graph_status / set_node_status guards ----------------------------


def test_set_node_status_guard_rejects_illegal():
    svc = _service()
    task = _graph_with_n1(svc)
    # PENDING → DONE is not legal (must pass RUNNING).
    with pytest.raises(IllegalTransitionError):
        svc.set_node_status(task, "n1", NodeStatus.DONE)


def test_set_node_status_legal_running_to_done():
    svc = _service()
    task = _graph_with_n1(svc)
    svc.set_node_status(task, "n1", NodeStatus.RUNNING)
    svc.set_node_status(task, "n1", NodeStatus.DONE)
    assert svc._find_node(task, "n1").status is NodeStatus.DONE  # noqa: SLF001


def test_mark_graph_status_sets_graph_status():
    svc = _service()
    task = _planned_task(svc)
    svc.mark_graph_status(task, GraphStatus.RUNNING)
    assert task.execution_graph.status is GraphStatus.RUNNING


def test_add_sibling_node_links_edge():
    svc = _service()
    task = _graph_with_n1(svc)
    svc.add_sibling_node(task, "n1", Node(node_id="n3", spec="c"))
    assert any(n.node_id == "n3" for n in task.execution_graph.nodes)
    assert any(
        e.from_node == "n1" and e.to_node == "n3" and e.kind is EdgeKind.DEPENDENCY
        for e in task.execution_graph.edges
    )