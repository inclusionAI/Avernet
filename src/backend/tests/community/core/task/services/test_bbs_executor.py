"""TDD for BbsExecutor (Phase 5, plan §5 / Case B/C/E).

Shared blackboard = TaskExecutionGraph.广场 bots read via TaskService query face
and write via on_event (no Scheduler tick — BBS is self-drive). Claim is CAS
(first bot wins; second gets None on the same node). BBS goal-FAIL parks the
graph at AWAITING_HUMAN_ACCEPT (task-level HUNG is gone — spec §2).
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.protocols import DispatchResult
from agentclaw.community.core.task.domain.events import EventKind, TaskEvent, next_seq
from agentclaw.community.core.task.domain.models import RunMode
from agentclaw.community.core.task.domain.models import (
    NodeStatus,
    Plan,
    SubTaskSpec,
    TaskStatus,
)
from agentclaw.community.core.task.services import BbsExecutorService, TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> tuple[TaskService, BbsExecutorService]:
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher(), None)
    return svc, BbsExecutorService(svc)


def _planned_with_dag(svc: TaskService, nodes=("n1", "n2")) -> str:
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    svc.finalize_plan(
        t.id,
        Plan(sub_tasks=[SubTaskSpec(node_id=n, spec=f"do {n}") for n in nodes], confidence=0.7),
    )
    task = svc.get(t.id)
    svc.spawn_build_dag(task)
    # advance to EXECUTING + plaza (BBS广场 operates on a plaza graph)
    task.status = TaskStatus.EXECUTING
    task.execution_graph.root_phase = TaskStatus.EXECUTING
    svc._task_repo.save(task)  # noqa: SLF001
    return t.id


# --- claim CAS (Case B) -----------------------------------------------------


def test_claim_first_bot_wins_second_gets_none_on_same_node():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    first = bbs.claim(tid, "bot-a")
    assert isinstance(first, DispatchResult)
    assert first.executor_id == "bot-a"
    assert first.run_mode is RunMode.BBS
    # node is now RUNNING — second bot gets None (no other PENDING node)
    second = bbs.claim(tid, "bot-b")
    assert second is None


def test_claim_advances_to_next_pending_node_when_one_taken():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1", "n2"))
    first = bbs.claim(tid, "bot-a")  # claims n1 (no edges → both unlocked)
    assert first is not None
    assert first.executor_id == "bot-a"
    # second bot advances to the next PENDING node (n2), does NOT re-claim n1
    second = bbs.claim(tid, "bot-b")
    assert second is not None
    assert second.executor_id == "bot-b"
    g = svc.get_task_graph(tid)
    n1 = next(n for n in g["nodes"] if n["node_id"] == "n1")
    n2 = next(n for n in g["nodes"] if n["node_id"] == "n2")
    assert n1["assignee"] == "bot-a"
    assert n2["assignee"] == "bot-b"


def test_claim_unknown_task_returns_none():
    svc, bbs = _service()
    assert bbs.claim("ghost", "bot-a") is None


# --- shared blackboard read/write (Case C) ---------------------------------


def test_post_progress_folds_event_into_blackboard():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    bbs.claim(tid, "bot-a")  # n1 → RUNNING
    # bot reports acceptance via广场续做
    task = svc.get(tid)
    seq = next_seq(svc._event_repo.latest_seq(tid))  # noqa: SLF001
    bbs.post_progress(
        TaskEvent(task_id=tid, seq=seq, kind=EventKind.NODE_ACCEPTED, payload={"node_id": "n1", "verifier": "bot-a"})
    )
    # shared blackboard reflects the fold
    g = svc.get_task_graph(tid)
    n1 = next(n for n in g["nodes"] if n["node_id"] == "n1")
    assert n1["status"] == "done"


def test_get_task_graph_is_the_shared_blackboard_for_all_bots():
    """Case C: every bot读 the same TaskExecutionGraph via the query face."""
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    bbs.claim(tid, "bot-a")
    # bot-b reads the same blackboard → sees bot-a's claim
    graph_for_b = svc.get_task_graph(tid)
    n1 = next(n for n in graph_for_b["nodes"] if n["node_id"] == "n1")
    assert n1["status"] == "running"
    assert n1["assignee"] == "bot-a"


# --- BBS goal fail → AWAITING_HUMAN_ACCEPT (Case E) -----------------------


def test_bbs_goal_reject_parks_awaiting_human_accept():
    svc, bbs = _service()
    tid = _planned_with_dag(svc, nodes=("n1",))
    # park at REVIEWING; BBS goal-FAIL no longer escalates to a task-level HUNG
    # terminal (spec §2) — it parks at AWAITING_HUMAN_ACCEPT like single_bot.
    task = svc.get(tid)
    task.status = TaskStatus.REVIEWING
    task.execution_graph.root_phase = TaskStatus.REVIEWING
    svc._task_repo.save(task)  # noqa: SLF001
    seq = next_seq(svc._event_repo.latest_seq(tid))  # noqa: SLF001
    bbs.post_progress(
        TaskEvent(
            task_id=tid,
            seq=seq,
            kind=EventKind.GOAL_REJECTED,
            payload={"verifier": "bbs", "verdict": "fail", "reason": "plaza stuck"},
        )
    )
    final = svc.get(tid)
    from agentclaw.community.core.task.domain.models import GraphStatus

    assert final.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    assert final.status is TaskStatus.REVIEWING  # not HUNG — stays REVIEWING


def test_post_progress_unknown_task_returns_none():
    svc, bbs = _service()
    assert bbs.post_progress(TaskEvent(task_id="ghost", seq=1, kind=EventKind.NODE_RUNNING)) is None