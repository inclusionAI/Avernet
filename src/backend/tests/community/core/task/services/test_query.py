"""TDD for TaskService query + 副屏 read face (Phase 2.4, plan §1.4b/§1.3b).

The query group is read-only — it NEVER mutates the aggregate or takes the
write lock. ``get_task_graph`` projects a ``TaskGraphView`` whose fields are a
superset of the state_machine canvas fields (§1.3b): node status/sub_status/
attempt/assignee/run_mode/collab_mode/artifacts/acceptance_result/
attempted_executors/sub_dag_ref + edge kind, and task-level root_phase/
graph_status/loop_round/definition_meta. ``get_sub_dag`` returns None when the
node has no ``SubDagRef`` (router → 404) and a self-describing stub when it does
(Phase 4 swaps the stub for the live ``SmGraphAdapter`` mapping).
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import (
    GraphStatus,
    NodeStatus,
    Plan,
    RunMode,
    SubTaskSpec,
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


def _task_with_graph(svc: TaskService) -> str:
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.finalize_plan(
        t.id,
        Plan(
            sub_tasks=[
                SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.SINGLE_BOT),
                SubTaskSpec(node_id="n2", spec="b", run_mode=RunMode.COOP_GROUP),
            ],
            edges=[],
            confidence=0.8,
        ),
    )
    t = svc.get(t.id)
    svc.spawn_build_dag(t)
    # mark n1 running + done, n2 coop with a sub-dag ref
    svc.set_node_status(t, "n1", NodeStatus.RUNNING)
    svc.set_node_status(t, "n1", NodeStatus.DONE)
    svc.spawn_sub_dag(t, "n2", ref_kind="bcs_sm", bcs_run_id="sm-1", group_id="g-1")
    svc.mark_graph_status(t, GraphStatus.ON_PLAZA)
    svc._task_repo.save(t)  # noqa: SLF001
    return t.id


# --- read-only / list / progress -------------------------------------------


def test_get_returns_snapshot_not_internal_ref():
    svc = _service()
    t = svc.create(title="t")
    snap = svc.get(t.id)
    assert snap is not None
    assert snap.id == t.id
    snap.spec.metadata.title = "mutated"
    # mutation on the returned snapshot must not bleed into the store
    assert svc.get(t.id).spec.metadata.title == "t"


def test_list_by_user_filters_and_limits():
    svc = _service()
    svc.create(title="a", user_id="u1")
    svc.create(title="b", user_id="u1")
    svc.create(title="c", user_id="u2")
    assert len(svc.list_by_user("u1")) == 2
    assert len(svc.list_by_user("u1", limit=1)) == 1
    assert svc.list_by_user("u2")[0].spec.metadata.title == "c"


def test_progress_projects_done_total_and_nodes():
    svc = _service()
    tid = _task_with_graph(svc)
    prog = svc.progress(tid)
    assert prog["task_id"] == tid
    assert prog["total"] == 2
    assert prog["done"] == 1
    assert len(prog["nodes"]) == 2
    assert prog["nodes"][1]["external"] is True  # n2 has a sub_dag ref


# --- get_task_graph field superset (§1.3b) ---------------------------------


def test_get_task_graph_carries_superset_fields():
    svc = _service()
    tid = _task_with_graph(svc)
    g = svc.get_task_graph(tid)
    # task-level
    assert g["task_id"] == tid
    assert g["root_phase"] in {"defined", "executing", "reviewing"}
    assert g["graph_status"] == "on_plaza"
    assert g["loop_round"] == 0
    assert g["definition_meta"]["node_count"] == 2
    # node-level superset of SM canvas fields
    n1 = next(n for n in g["nodes"] if n["node_id"] == "n1")
    for field in (
        "node_id",
        "display_name",
        "status",
        "sub_status",
        "attempt",
        "assignee",
        "run_mode",
        "artifacts",
        "acceptance_result",
        "attempted_executors",
        "sub_dag_ref",
    ):
        assert field in n1, f"missing superset field {field}"
    assert n1["status"] == "done"
    assert n1["run_mode"] == "single_bot"
    # n2 carries the sub_dag_ref pointer (drill-down hint)
    n2 = next(n for n in g["nodes"] if n["node_id"] == "n2")
    assert n2["sub_dag_ref"] is not None
    assert n2["sub_dag_ref"]["bcs_run_id"] == "sm-1"


def test_get_task_graph_unknown_task_returns_none():
    svc = _service()
    assert svc.get_task_graph("ghost") is None


# --- get_node_detail --------------------------------------------------------


def test_get_node_detail_returns_attempt_and_acceptance():
    svc = _service()
    tid = _task_with_graph(svc)
    d = svc.get_node_detail(tid, "n1")
    assert d["node_id"] == "n1"
    assert d["status"] == "done"
    assert d["run_mode"] == "single_bot"
    assert d["attempt"] == 0  # no attempted record on n1 in this fixture
    assert d["sub_dag_ref"] is None


def test_get_node_detail_unknown_node_returns_none():
    svc = _service()
    tid = _task_with_graph(svc)
    assert svc.get_node_detail(tid, "ghost") is None


# --- get_sub_dag stub path --------------------------------------------------


def test_get_sub_dag_none_without_ref():
    svc = _service()
    tid = _task_with_graph(svc)
    # n1 has no sub_dag ref → None (router → 404)
    assert svc.get_sub_dag(tid, "n1") is None


def test_get_sub_dag_fallback_stub_without_bcs_port():
    # Phase 4: get_sub_dag maps live via SmGraphAdapter when a BCS Port is wired.
    # test_query builds TaskService WITHOUT a BCS Port → falls back to the
    # self-describing stub (drill_down_live=False). Live mapping is covered by
    # test_get_sub_dag_live.py.
    svc = _service()
    tid = _task_with_graph(svc)
    sub = svc.get_sub_dag(tid, "n2")
    assert sub is not None
    assert sub["definition_meta"]["bcs_run_id"] == "sm-1"
    assert sub["definition_meta"]["drill_down_live"] is False
    assert sub["nodes"][0]["run_mode"] == "coop_group"


@pytest.mark.asyncio
async def test_subscribe_task_graph_yields_current_snapshot():
    svc = _service()
    tid = _task_with_graph(svc)
    snaps = []
    async for s in svc.subscribe_task_graph(tid):
        snaps.append(s)
    assert len(snaps) == 1
    assert snaps[0]["task_id"] == tid