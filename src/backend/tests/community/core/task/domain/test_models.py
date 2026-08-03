"""TDD for task domain models (Phase 0.1). Red first, then green via models.py.

Covers plan.md §1.1-§1.3: Task aggregate root (spec + execution_graph two faces),
enums, AttemptedRecord absorbing RouteHop, sub_dag read-only projection.
"""
from __future__ import annotations

from dataclasses import asdict

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    AttemptTrigger,
    AttemptedRecord,
    CollabMode,
    Constraint,
    ConstraintKind,
    Deliverable,
    DeliverableType,
    Edge,
    EdgeKind,
    GraphStatus,
    Node,
    NodeStatus,
    ProgressNode,
    RouteClass,
    RunMode,
    SubDagRef,
    Task,
    TaskExecutionGraph,
    TaskGoal,
    TaskSource,
    TaskSpec,
    TaskSpecMetadata,
    GraphStatus,
)


def test_graphstatus_has_9_states_with_3_terminals():
    assert set(GraphStatus) == {
        GraphStatus.DRAFTING,
        GraphStatus.DEFINED,
        GraphStatus.RUNNING,
        GraphStatus.HUMAN_REQUIRED,
        GraphStatus.BBS_ACTIVE,
        GraphStatus.REVIEWING,
        GraphStatus.DONE,
        GraphStatus.CANCELLED,
        GraphStatus.FAILED,
    }
    assert {GraphStatus.DONE, GraphStatus.CANCELLED, GraphStatus.FAILED} <= set(GraphStatus)


def test_node_status_has_6_states():
    assert set(NodeStatus) == {
        NodeStatus.PENDING,
        NodeStatus.RUNNING,
        NodeStatus.DONE,
        NodeStatus.FAILED,
        NodeStatus.SKIPPED,
        NodeStatus.HUNG,
    }


def test_graphstatus_edgekind_runmode_collabmode_routeclass_enum_shapes():
    assert set(GraphStatus) == {
        GraphStatus.DRAFTING,
        GraphStatus.DEFINED,
        GraphStatus.RUNNING,
        GraphStatus.HUMAN_REQUIRED,
        GraphStatus.BBS_ACTIVE,
        GraphStatus.REVIEWING,
        GraphStatus.DONE,
        GraphStatus.CANCELLED,
        GraphStatus.FAILED,
    }
    assert set(EdgeKind) == {
        EdgeKind.DEPENDENCY,
        EdgeKind.CONDITIONAL,
        EdgeKind.FALLBACK,
        EdgeKind.PARALLEL_SYNC,
    }
    assert set(RunMode) == {RunMode.SINGLE_BOT, RunMode.COOP_GROUP, RunMode.BBS}
    assert set(CollabMode) == {
        CollabMode.CHAT,
        CollabMode.MANAGER_WORKER,
        CollabMode.STATE_MACHINE,
    }
    assert set(RouteClass) == {
        RouteClass.C1,
        RouteClass.C2,
        RouteClass.C3,
        RouteClass.C4,
        RouteClass.C5,
    }


def test_task_construct_minimal_execution_graph_none():
    spec = TaskSpec(metadata=TaskSpecMetadata(id="t1", title="fix PR"))
    task = Task(id="t1", user_id="u1", source=TaskSource.IM, spec=spec)
    assert task.execution_graph is None
    assert task.loop_round == 0
    assert task.status is GraphStatus.DRAFTING  # 无图时 delegate 默认 DRAFTING


def test_task_spec_progressive_defaults():
    spec = TaskSpec(metadata=TaskSpecMetadata(id="t1", title="x"))
    assert spec.context.background == ""
    assert spec.context.constraints == []
    assert spec.goal is None
    assert spec.deliverables == []
    assert spec.execution is None
    # TaskSpec 不挂 plan(2026-08-03:Plan 退场,分解运行期入图)
    assert not hasattr(spec, "plan")


def test_node_defaults_status_pending_and_properties():
    node = Node(node_id="n1", spec="do X")
    assert node.status is NodeStatus.PENDING
    assert node.run_mode is None
    assert node.artifacts == []
    assert node.attempted_executors == []
    assert node.properties["retry_count"] == 0
    assert node.properties["max_attempts"] == 2
    assert node.properties["loop_round"] == 0
    assert node.sub_dag is None


def test_attempted_record_defaults_trigger_routed_outcome_none():
    rec = AttemptedRecord(executor_id="b1", paradigm=RunMode.SINGLE_BOT, round=1)
    assert rec.trigger is AttemptTrigger.ROUTED
    assert rec.outcome is None
    assert rec.route_class is None


def test_attempted_record_absorbs_route_hop_fields():
    rec = AttemptedRecord(
        executor_id="b1",
        paradigm=RunMode.SINGLE_BOT,
        round=1,
        route_class=RouteClass.C3,
        from_mode=RunMode.SINGLE_BOT,
        to_mode=RunMode.SINGLE_BOT,
        trigger=AttemptTrigger.REPLANNED,
        note="reroute after accept fail",
    )
    assert rec.route_class is RouteClass.C3
    assert rec.trigger is AttemptTrigger.REPLANNED


def test_execution_graph_status_default_drafting():
    g = TaskExecutionGraph()
    assert g.status is GraphStatus.DRAFTING
    assert g.nodes == []
    assert g.edges == []
    g2 = TaskExecutionGraph(status=GraphStatus.RUNNING)
    assert g2.status is GraphStatus.RUNNING


def test_acceptance_criteria_polymorphic_bag():
    ac = AcceptanceCriteria(
        kind=AcceptanceCriteriaKind.BEHAVIOR,
        properties={"assertion": "returns 200"},
    )
    assert ac.kind is AcceptanceCriteriaKind.BEHAVIOR
    assert ac.properties["assertion"] == "returns 200"


def test_goal_deliverable_constraint_shapes():
    goal = TaskGoal(
        objective="align PR",
        acceptances=[AcceptanceCriteria(kind=AcceptanceCriteriaKind.INVARIANT)],
    )
    assert goal.objective == "align PR"
    assert goal.acceptances[0].kind is AcceptanceCriteriaKind.INVARIANT
    d = Deliverable(type=DeliverableType.CODE, location="repo:pr/1")
    assert d.type is DeliverableType.CODE
    c = Constraint(kind=ConstraintKind.HARD, text="no breaking changes")
    assert c.kind is ConstraintKind.HARD


def test_node_sub_dag_optional():
    """Node.sub_dag holds a SubDagRef (external-run pointer), not an embedded
    TaskExecutionGraph. The old embedded-graph shape was retired in plan §1.3a:
    cooperative-group nodes keep the group-self-loop invariant (no per-child
    state tracking) and the canvas drills down via a live BCS fetch."""
    ref = SubDagRef(
        ref_kind="bcs_state_machine",
        bcs_run_id="sm-abc",
        group_id="g1",
    )
    node = Node(node_id="n1", spec="x", sub_dag=ref)
    assert node.sub_dag is not None
    assert node.sub_dag.ref_kind == "bcs_state_machine"
    assert node.sub_dag.bcs_run_id == "sm-abc"
    assert node.sub_dag.group_id == "g1"
    assert node.sub_dag.workflow_yaml_snapshot is None  # no child state tracked


def test_sub_dag_ref_defaults():
    """SubDagRef: ref_kind/bcs_run_id/group_id required; yaml snapshot optional."""
    ref = SubDagRef(ref_kind="bcs_state_machine", bcs_run_id="sm-1", group_id="g1")
    assert ref.workflow_yaml_snapshot is None
    # snapshot round-trips when provided (audit/replay only, not live state)
    ref2 = SubDagRef(
        ref_kind="bcs_state_machine",
        bcs_run_id="sm-2",
        group_id="g2",
        workflow_yaml_snapshot="runtime:\n  kind: state_machine\n",
    )
    assert ref2.workflow_yaml_snapshot.startswith("runtime:")


def test_sub_dag_ref_serializable_via_asdict():
    ref = SubDagRef(ref_kind="bcs_state_machine", bcs_run_id="sm-3", group_id="g3")
    d = asdict(ref)
    assert d["ref_kind"] == "bcs_state_machine"
    assert d["bcs_run_id"] == "sm-3"
    assert d["workflow_yaml_snapshot"] is None


def test_progress_node_projection_shape():
    p = ProgressNode(node_id="n1", seq=3, way="single_bot")
    assert p.status is NodeStatus.PENDING
    assert p.external is False


def test_models_serializable_via_asdict():
    spec = TaskSpec(
        metadata=TaskSpecMetadata(id="t1", title="x"),
        goal=TaskGoal(objective="o"),
    )
    task = Task(
        id="t1",
        user_id="u1",
        source=TaskSource.API,
        spec=spec,
        execution_graph=TaskExecutionGraph(
            status=GraphStatus.RUNNING,
            nodes=[Node(node_id="n1", spec="s")],
            edges=[Edge(edge_id="e1", from_node="n1", to_node="n1")],
        ),
    )
    d = asdict(task)
    assert d["id"] == "t1"
    assert d["execution_graph"]["status"] == GraphStatus.RUNNING
    assert d["spec"]["goal"]["objective"] == "o"
    assert d["execution_graph"]["nodes"][0]["node_id"] == "n1"
