"""TDD for the v2 全生命周期重构 surface(tasks plan §17/§18/§20)。

Covers the additive v2 design surface landed so far:
- ``aggregate_verdict`` 纯函数完成判断统一(FR-GRAPH-07b)
- ``GRAPH_TRANSITIONS`` guard(plan §5.1/§18.1-8)
- ``DecomposerPort.decompose_subtasks`` depth(§3.5/§11)
- ``TaskService`` 图操作 add_node/add_edge/update_state/retrieve_state/fold(§4.3/§8)
- ``_render_kind`` 副屏渲染分类(O-P5)
- ``NodeType`` / ``StateSemantics`` 枚举

大局点(全生命周期 action-node graph / 搜推先行 / exec-aggregate 触发 / BBS 同图 / hang
三终止 / 12 条 E2E)落 tasks.md T-16..T-36,本文件先覆盖已落地的纯函数 + 图操作写口。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    AttemptOutcome,
    GraphStatus,
    NodeType,
    Plan,
    StateSemantics,
    SubTaskSpec,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
    can_graph_transition,
    require_graph_transition,
)
from agentclaw.community.core.task.protocols import aggregate_verdict
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.core.task.services.decomposer_service import DecomposerService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


# --- aggregate_verdict (FR-GRAPH-07b) ---------------------------------------


def test_aggregate_verdict_all_pass_yields_done():
    acs = [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "ac0"})]
    children = [{"outcome": AttemptOutcome.PASS}, {"outcome": AttemptOutcome.PASS}]
    verdict, unmet = aggregate_verdict(acs, children)
    assert verdict is AttemptOutcome.PASS
    assert unmet == []


def test_aggregate_verdict_no_child_pass_yields_fail_with_unmet():
    acs = [AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "ac0"})]
    children = [{"outcome": AttemptOutcome.FAIL}]
    verdict, unmet = aggregate_verdict(acs, children)
    assert verdict is AttemptOutcome.FAIL
    assert unmet == ["ac0"]


def test_aggregate_verdict_no_acceptances_conservative():
    # 无 AC + children 全 PASS → DONE;否则 FAIL
    assert aggregate_verdict([], [{"outcome": AttemptOutcome.PASS}])[0] is AttemptOutcome.PASS
    assert aggregate_verdict([], [{"outcome": AttemptOutcome.FAIL}])[0] is AttemptOutcome.FAIL


# --- GRAPH_TRANSITIONS guard (§5.1/§18.1-8) ---------------------------------


def test_graph_transitions_legal_paths():
    assert can_graph_transition(GraphStatus.ON_PLAZA, GraphStatus.AWAITING_HUMAN_ACCEPT)
    assert can_graph_transition(GraphStatus.AWAITING_HUMAN_ACCEPT, GraphStatus.ON_PLAZA)
    assert can_graph_transition(GraphStatus.AWAITING_HUMAN_ACCEPT, GraphStatus.AWAITING_HUMAN_ADJUST)
    assert can_graph_transition(GraphStatus.ON_PLAZA, GraphStatus.VERIFIED)


def test_graph_transitions_illegal_raises():
    # VERIFIED 终态无出边;AWAITING_HUMAN_ADJUST 不能直接 VERIFIED
    with pytest.raises(IllegalTransitionError):
        require_graph_transition(GraphStatus.VERIFIED, GraphStatus.ON_PLAZA)
    with pytest.raises(IllegalTransitionError):
        require_graph_transition(GraphStatus.AWAITING_HUMAN_ADJUST, GraphStatus.VERIFIED)


def test_mark_graph_status_guards_transition():
    svc = _service()
    t = svc.create(title="t")
    p = Plan(sub_tasks=[SubTaskSpec(node_id="n1", spec="a")], confidence=0.7)
    svc.finalize_plan(t.id, p)
    task = svc.get(t.id)
    svc.spawn_build_dag(task)  # graph created, ON_PLAZA
    # legal: ON_PLAZA -> AWAITING_HUMAN_ACCEPT (mark_hang)
    svc.mark_graph_status(task, GraphStatus.AWAITING_HUMAN_ACCEPT)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    # illegal: AWAITING_HUMAN_ACCEPT -> VERIFIED (not in table)
    with pytest.raises(IllegalTransitionError):
        svc.mark_graph_status(task, GraphStatus.VERIFIED)


# --- DecomposerPort.decompose_subtasks depth (§3.5/§11) ---------------------


def test_decompose_subtasks_top_level_depth_zero():
    from agentclaw.community.core.task.domain.models import TaskState

    subs = DecomposerService().decompose_subtasks("a; b; c", TaskState())
    assert len(subs) == 3
    assert all(s.depth == 0 for s in subs)  # 顶层 → 根 subtask depth=0


def test_decompose_subtasks_child_depth_parent_plus_one():
    from agentclaw.community.core.task.domain.models import TaskState

    state = TaskState(public={"__decompose_parent_depth__": 2})
    subs = DecomposerService().decompose_subtasks("a; b", state)
    assert all(s.depth == 3 for s in subs)  # 父 depth=2 → children depth=3


# --- NodeType / render_kind (§3.1/O-P5) -------------------------------------


def test_render_kind_maps_control_gate_and_system_bridge():
    svc = _service()
    assert svc._render_kind(NodeType.EXEC_AGGREGATE) == "control-gate"
    assert svc._render_kind(NodeType.GOAL_VERIFY) == "control-gate"
    assert svc._render_kind(NodeType.EXEC_ACCEPT) == "control-gate"
    assert svc._render_kind(NodeType.DISPATCH) == "system-bridge"
    assert svc._render_kind(NodeType.MARK_HANG) == "system-bridge"
    assert svc._render_kind(NodeType.BBS_DISPATCH) == "system-bridge"
    assert svc._render_kind(NodeType.BOT_SEARCH) == "exec"


# --- TaskService graph-operation write face (§4.3/§8) -----------------------


def _graph_task(svc: TaskService):
    t = svc.create(title="t")
    p = Plan(sub_tasks=[SubTaskSpec(node_id="n1", spec="a")], confidence=0.7)
    svc.finalize_plan(t.id, p)
    task = svc.get(t.id)
    svc.spawn_build_dag(task)
    return task


def test_add_node_appends_node_edge_and_subtask_state():
    svc = _service()
    task = _graph_task(svc)
    child = SubTaskSpec(node_id="n2", spec="child")
    svc.add_node(task.id, child, parent_node="n1", node_type=NodeType.DISPATCH, executor="bot1")
    task = svc.get(task.id)
    ids = [n.node_id for n in task.execution_graph.nodes]
    assert "n2" in ids
    assert any(e.from_node == "n1" and e.to_node == "n2" for e in task.execution_graph.edges)
    # SubtaskState 分区已建
    assert "n2" in task.execution_graph.state.subtasks
    node2 = next(n for n in task.execution_graph.nodes if n.node_id == "n2")
    assert node2.node_type is NodeType.DISPATCH
    assert node2.assignee == "bot1"


def test_update_state_merge_and_retrieve():
    svc = _service()
    task = _graph_task(svc)
    svc.update_state(task.id, None, {"phase": "EXECUTING", "constraint": "py"}, StateSemantics.MERGE)
    out = svc.retrieve_state(task.id, None)
    assert out["public"]["phase"] == "EXECUTING"
    assert out["public"]["constraint"] == "py"
    # scope partition
    svc.update_state(
        task.id, "n1", {"execution_context": {"k": "v"}}, StateSemantics.MERGE
    )
    out = svc.retrieve_state(task.id, "n1")
    assert out["subtask"]["node_id"] == "n1"
    assert out["subtask"]["execution_context"] == {"k": "v"}


def test_snapshot_captures_current_fold():
    svc = _service()
    task = _graph_task(svc)
    # add_node 持久化一个节点(snapshot 经 repo 重读,需落盘)
    svc.add_node(
        task.id, SubTaskSpec(node_id="n2", spec="x"), parent_node="n1", node_type=NodeType.DISPATCH
    )
    snap = svc.snapshot(task.id)
    assert snap.task_id == task.id
    assert snap.graph.graph_status is GraphStatus.ON_PLAZA
    assert any(n.node_id == "n2" for n in snap.graph.nodes)