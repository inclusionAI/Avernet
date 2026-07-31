"""TDD for SmGraphAdapter (Phase 4.2b, plan §1.3a/§1.3b/§1.3c).

``to_sub_dag_view`` is a pure mapping of a BCS ``StateMachineRunGraphView``
snapshot into a ``TaskGraphView`` subtree dict. Asserts the §1.3b field coverage
(every SM canvas field has a task-graph landing) and the §1.3c status mapping.
``fetch_sub_dag_view`` is the IO seam — tested with a fake Port.
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import SubDagRef
from agentclaw.community.core.task.services.graph_adapter import (
    SmGraphAdapter,
    to_sub_dag_view,
)


def _sm_snapshot() -> dict:
    return {
        "run": {
            "run_id": "sm-1",
            "status": "running",
            "input": {"goal": "do x"},
            "output": None,
        },
        "definition": {
            "id": "def-1",
            "version": 3,
            "name": "coop-flow",
            "graph_mode": "acyclic",
            "initial_nodes": ["n1"],
        },
        "nodes": [
            {
                "node_id": "n1",
                "display_name": "research",
                "kind": "bot_task",
                "final_output": False,
                "status": "running",
                "attempt": 2,
                "sub_status": "awaiting_response",
                "assignee": "bot-a",
                "started_at": "2026-07-29T00:00:00Z",
                "error": None,
            },
            {
                "node_id": "n2",
                "display_name": "deliver",
                "kind": "bot_task",
                "final_output": True,
                "status": "completed",
                "attempt": 1,
                "sub_status": "judging",
                "assignee": "bot-b",
                "artifact_text": "result artifact",
                "judge_outputs": [{"decision": "pass"}],
            },
        ],
        "edges": [
            {"source": "n1", "target": "n2", "outcome": "ok", "guard": "n1.done"},
        ],
    }


def _ref() -> SubDagRef:
    return SubDagRef(ref_kind="bcs_sm", bcs_run_id="sm-1", group_id="g-1")


# --- §1.3b field coverage ---------------------------------------------------


def test_definition_meta_carries_sm_definition_fields():
    view = to_sub_dag_view(_sm_snapshot(), task_id="t1", ref=_ref())
    meta = view["definition_meta"]
    assert meta["name"] == "coop-flow"
    assert meta["graph_mode"] == "acyclic"
    assert meta["initial_nodes"] == ["n1"]
    assert meta["definition_id"] == "def-1"
    assert meta["definition_version"] == 3
    assert meta["bcs_run_id"] == "sm-1"
    assert meta["drill_down_live"] is True


def test_node_fields_superset_cover_sm_canvas():
    view = to_sub_dag_view(_sm_snapshot(), task_id="t1", ref=_ref())
    n1 = view["nodes"][0]
    # §1.3b: every SM canvas field has a task-graph landing
    for field in (
        "node_id",
        "display_name",
        "run_mode",
        "collab_mode",
        "status",
        "sub_status",
        "attempt",
        "assignee",
        "started_at",
        "completed_at",
        "is_final_output",
        "attempted_executors",
        "artifacts",
        "acceptance_result",
        "properties",
    ):
        assert field in n1, f"missing {field}"
    assert n1["node_id"] == "n1"
    assert n1["run_mode"] == "single_bot"  # kind=bot_task → single_bot
    assert n1["collab_mode"] == "state_machine"
    assert n1["assignee"] == "bot-a"
    assert n1["attempt"] == 2
    assert n1["properties"]["retry_count"] == 1  # attempt - 1


def test_artifact_text_maps_to_artifacts():
    view = to_sub_dag_view(_sm_snapshot(), task_id="t1", ref=_ref())
    n2 = view["nodes"][1]
    assert any(a.get("text") == "result artifact" for a in n2["artifacts"])


def test_judge_outputs_map_to_acceptance_result():
    view = to_sub_dag_view(_sm_snapshot(), task_id="t1", ref=_ref())
    n2 = view["nodes"][1]
    assert n2["acceptance_result"] == "pass"


# --- §1.3c status mapping ---------------------------------------------------


def test_status_mapping_sm_to_task():
    cases = {
        "pending": "pending",
        "ready": "pending",
        "running": "running",
        "completed": "done",
        "failed": "failed",
        "retry_scheduled": "failed",
        "skipped": "skipped",
    }
    for sm_status, expected in cases.items():
        snap = {"run": {"status": "running"}, "definition": {}, "nodes": [{"node_id": "n", "status": sm_status}], "edges": []}
        view = to_sub_dag_view(snap, task_id="t", ref=_ref())
        assert view["nodes"][0]["status"] == expected, f"{sm_status}→{expected}"


def test_sm_missing_fields_default_safely():
    snap = {"run": {}, "definition": {}, "nodes": [{}], "edges": []}
    view = to_sub_dag_view(snap, task_id="t", ref=_ref())
    n = view["nodes"][0]
    assert n["status"] == "pending"
    assert n["run_mode"] is None
    assert n["attempted_executors"] == []
    assert n["properties"]["error_msg"] is None


# --- edge mapping ----------------------------------------------------------


def test_edge_with_outcome_is_conditional_carries_guard():
    view = to_sub_dag_view(_sm_snapshot(), task_id="t1", ref=_ref())
    e = view["edges"][0]
    assert e["from_node"] == "n1"
    assert e["to_node"] == "n2"
    assert e["kind"] == "conditional"
    assert e["outcome"] == "ok"
    assert e["guard"] == "n1.done"


def test_edge_without_outcome_is_dependency():
    snap = {"run": {}, "definition": {}, "nodes": [], "edges": [{"source": "a", "target": "b"}]}
    view = to_sub_dag_view(snap, task_id="t", ref=_ref())
    assert view["edges"][0]["kind"] == "dependency"


# --- fetch_sub_dag_view (IO seam) ------------------------------------------


class _FakeBcs:
    def __init__(self, snapshot) -> None:
        self._snap = snapshot
        self.calls: list = []

    def fetch_state_machine_run_graph(self, bcs_run_id: str):
        self.calls.append(bcs_run_id)
        return self._snap

    def fetch_node_detail(self, bcs_run_id: str, node_id: str):
        return {}


def test_fetch_sub_dag_view_pulls_live_and_maps():
    adapter = SmGraphAdapter(_FakeBcs(_sm_snapshot()))
    view = adapter.fetch_sub_dag_view("t1", "n1", _ref())
    assert view is not None
    assert view["task_id"] == "t1"
    assert len(view["nodes"]) == 2
    assert view["definition_meta"]["bcs_run_id"] == "sm-1"


def test_fetch_sub_dag_view_empty_snapshot_returns_none():
    adapter = SmGraphAdapter(_FakeBcs({}))
    assert adapter.fetch_sub_dag_view("t1", "n1", _ref()) is None


def test_fetch_sub_dag_view_port_failure_returns_none():
    class _BoomBcs:
        def fetch_state_machine_run_graph(self, bcs_run_id: str):
            raise RuntimeError("boom")

        def fetch_node_detail(self, bcs_run_id: str, node_id: str):
            return {}

    adapter = SmGraphAdapter(_BoomBcs())
    assert adapter.fetch_sub_dag_view("t1", "n1", _ref()) is None