"""TDD for TaskService.get_sub_dag live path (Phase 4.9a, plan §1.3a).

Replaces the Phase 2 stub: when a cooperative-group node carries a
:class:`SubDagRef`, ``get_sub_dag`` live-fetches the BCS SM run graph via
:class:`SmGraphAdapter` and returns a mapped ``TaskGraphView`` subtree.
Non-coop node or no ref → None (router → 404).
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import Plan, SubTaskSpec
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


class _FakeBcs:
    def __init__(self, snapshot) -> None:
        self._snap = snapshot
        self.calls: list = []

    def fetch_state_machine_run_graph(self, bcs_run_id: str):
        self.calls.append(bcs_run_id)
        return self._snap

    def fetch_node_detail(self, bcs_run_id: str, node_id: str):
        return {}


def _sm_snapshot() -> dict:
    return {
        "run": {"run_id": "sm-9", "status": "running", "output": None},
        "definition": {"id": "def", "version": 1, "name": "coop", "graph_mode": "acyclic", "initial_nodes": ["c1"]},
        "nodes": [
            {"node_id": "c1", "display_name": "research", "kind": "bot_task", "status": "running", "attempt": 1, "sub_status": "awaiting_response", "assignee": "bot-z"},
            {"node_id": "c2", "display_name": "deliver", "kind": "bot_task", "status": "completed", "attempt": 1, "final_output": True, "artifact_text": "out", "judge_outputs": [{"decision": "pass"}]},
        ],
        "edges": [{"source": "c1", "target": "c2", "outcome": "ok", "guard": "c1.done"}],
    }


def _service(bcs=None) -> tuple[TaskService, _FakeBcs]:
    fake = bcs or _FakeBcs(_sm_snapshot())
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher(), fake)
    return svc, fake


def _task_with_coop_node(svc: TaskService) -> str:
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    svc.finalize_plan(
        t.id,
        Plan(sub_tasks=[SubTaskSpec(node_id="n1", spec="a"), SubTaskSpec(node_id="n2", spec="b")], confidence=0.8),
    )
    task = svc.get(t.id)
    svc.spawn_build_dag(task)
    svc.spawn_sub_dag(task, "n2", ref_kind="bcs_sm", bcs_run_id="sm-9", group_id="g-9")
    svc._task_repo.save(task)  # noqa: SLF001
    return t.id


def test_get_sub_dag_live_maps_sm_run_graph():
    svc, fake = _service()
    tid = _task_with_coop_node(svc)
    sub = svc.get_sub_dag(tid, "n2")
    assert sub is not None
    # live fetch happened
    assert fake.calls == ["sm-9"]
    assert sub["definition_meta"]["bcs_run_id"] == "sm-9"
    assert sub["definition_meta"]["drill_down_live"] is True
    # mapped 2 nodes from the SM run graph
    assert len(sub["nodes"]) == 2
    assert sub["nodes"][0]["node_id"] == "c1"
    assert sub["nodes"][0]["status"] == "running"
    assert sub["nodes"][1]["acceptance_result"] == "pass"
    # edge mapped with outcome → conditional
    assert sub["edges"][0]["kind"] == "conditional"


def test_get_sub_dag_non_coop_node_returns_none():
    svc, _ = _service()
    tid = _task_with_coop_node(svc)
    # n1 has no sub_dag ref
    assert svc.get_sub_dag(tid, "n1") is None


def test_get_sub_dag_no_ref_returns_none():
    svc, _ = _service()
    tid = _task_with_coop_node(svc)
    assert svc.get_sub_dag(tid, "ghost") is None


def test_get_sub_dag_unknown_task_returns_none():
    svc, _ = _service()
    assert svc.get_sub_dag("ghost", "n1") is None


def test_get_sub_dag_without_bcs_port_falls_back_to_stub():
    # No BCS Port wired → self-describing stub (drill_down_live=False)
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher(), None)
    tid = _task_with_coop_node(svc)
    sub = svc.get_sub_dag(tid, "n2")
    assert sub is not None
    assert sub["definition_meta"]["drill_down_live"] is False