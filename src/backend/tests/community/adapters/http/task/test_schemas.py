"""TDD for task canvas schemas (Phase 0.6, plan §1.4b / §1.3b).

TaskGraphView/TaskNodeView/TaskEdgeView/TaskNodeDetailView/SubDagRefView form
the wire contract for the secondary dynamic-workflow canvas. They are a
SUPERSET of the state-machine canvas fields (AC-12): every SM canvas field
has a corresponding landing here; the task graph adds deepresearch-DAG-only
fields (run_mode/collab_mode/attempted_executors/acceptance_result/...).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.schemas import (
    SubDagRefView,
    TaskEdgeView,
    TaskGraphView,
    TaskNodeDetailView,
    TaskNodeView,
)


# --- SubDagRefView ----------------------------------------------------------

def test_sub_dag_ref_view_required_fields():
    ref = SubDagRefView(ref_kind="bcs_state_machine", bcs_run_id="sm-1", group_id="g1")
    assert ref.ref_kind == "bcs_state_machine"
    assert ref.bcs_run_id == "sm-1"
    assert ref.group_id == "g1"
    assert ref.workflow_yaml_snapshot is None  # optional


def test_sub_dag_ref_view_rejects_missing_bcs_run_id():
    with pytest.raises(ValidationError):
        SubDagRefView(ref_kind="bcs_state_machine", group_id="g1")  # type: ignore[call-arg]


# --- TaskNodeView: super-set of SM canvas fields (AC-12) --------------------

def test_task_node_view_sm_canvas_fields_present():
    # AC-12: SM canvas node fields must all land on TaskNodeView.
    n = TaskNodeView(
        node_id="n1",
        display_name="写作者",
        run_mode="single_bot",
        collab_mode=None,
        status="running",
        sub_status="awaiting_response",
        attempt=1,
        assignee="bot-1",
        started_at=1000,
        completed_at=None,
        is_final_output=False,
    )
    assert n.node_id == "n1"
    assert n.status == "running"
    assert n.sub_status == "awaiting_response"
    assert n.attempt == 1
    assert n.assignee == "bot-1"
    assert n.is_final_output is False


def test_task_node_view_deepresearch_superset_fields_present():
    # task-graph-only fields beyond SM canvas (run_mode/collab_mode/attempted/
    # acceptance_result/artifacts/properties).
    n = TaskNodeView(
        node_id="n1",
        display_name="n",
        run_mode="coop_group",
        collab_mode="state_machine",
        status="pending",
        attempted_executors=[],
        artifacts=[],
        acceptance_result=None,
        properties={"retry_count": 0, "max_attempts": 2, "ready": True},
    )
    assert n.run_mode == "coop_group"
    assert n.collab_mode == "state_machine"
    assert n.properties["ready"] is True


def test_task_node_view_defaults():
    n = TaskNodeView(node_id="n1", display_name="n")
    assert n.run_mode is None
    assert n.status == "pending"
    assert n.attempt is None
    assert n.attempted_executors == []
    assert n.artifacts == []
    assert n.sub_dag_ref is None


def test_task_node_view_carries_sub_dag_ref():
    n = TaskNodeView(
        node_id="n1",
        display_name="群节点",
        run_mode="coop_group",
        sub_dag_ref=SubDagRefView(
            ref_kind="bcs_state_machine", bcs_run_id="sm-9", group_id="g9"
        ),
    )
    assert n.sub_dag_ref is not None
    assert n.sub_dag_ref.bcs_run_id == "sm-9"


# --- TaskEdgeView: outcome/guard align SM canvas ----------------------------

def test_task_edge_view_outcome_and_guard():
    e = TaskEdgeView(
        edge_id="e1",
        from_node="n1",
        to_node="n2",
        kind="conditional",
        outcome="pass",
        guard="criteria_met",
    )
    assert e.from_node == "n1"
    assert e.to_node == "n2"
    assert e.outcome == "pass"
    assert e.guard == "criteria_met"


def test_task_edge_view_optional_outcome_guard():
    e = TaskEdgeView(edge_id="e1", from_node="n1", to_node="n2", kind="dependency")
    assert e.outcome is None
    assert e.guard is None


# --- TaskGraphView ----------------------------------------------------------

def test_task_graph_view_shape():
    g = TaskGraphView(
        task_id="t1",
        status="running",
        loop_round=0,
        nodes=[TaskNodeView(node_id="n1", display_name="n")],
        edges=[TaskEdgeView(edge_id="e1", from_node="n1", to_node="n1", kind="dependency")],
    )
    assert g.task_id == "t1"
    assert g.status == "running"
    assert g.nodes[0].node_id == "n1"
    assert g.edges[0].from_node == "n1"
    assert g.definition_meta is None  # optional top-level meta


# --- TaskNodeDetailView: superset of node + delivery correlation ------------

def test_task_node_detail_view_carries_full_detail():
    d = TaskNodeDetailView(
        node_id="n1",
        display_name="n",
        status="failed",
        attempt=3,
        run_mode="single_bot",
        properties={"error_msg": "boom", "max_attempts": 2},
    )
    assert d.status == "failed"
    assert d.properties["error_msg"] == "boom"


def test_task_schemas_do_not_import_core():
    # wire schemas must stay adapter-boundary; no core domain import
    import agentclaw.community.adapters.http.task.schemas as s
    import inspect
    src = inspect.getsource(s)
    assert "agentclaw.community.core" not in src, "schemas must not import core"