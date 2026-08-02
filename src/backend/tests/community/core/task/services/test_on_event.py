"""TDD for TaskService.on_event event-fold (Phase 2.3, plan §2.3).

``on_event`` is the only state write path. Each kind is guarded + folded into
the aggregate; the event log is appended (single writer). Goal verdict split:
PASS → graph VERIFIED + DONE; FAIL (single_bot OR bbs) → AWAITING_HUMAN_ACCEPT
(task-level HUNG is gone — spec §2; unrecoverable blockage is task FAILED via
the Scheduler, not the goal fold). The fold never self-invokes check_node /
check_goal (those are Scheduler / owner-bot SKILL concerns, not TaskService's).
"""
from __future__ import annotations


from agentclaw.community.core.task.domain.events import (
    EventKind,
    TaskEvent,
    next_seq,
)
from agentclaw.community.core.task.domain.models import (
    NodeStatus,
    Plan,
    SubTaskSpec,
    TaskStatus,
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


def _planned_with_dag(svc: TaskService, task_id: str) -> None:
    svc.clarify(task_id, {"summary": "s"})
    svc.finalize_plan(
        task_id,
        Plan(sub_tasks=[SubTaskSpec(node_id="n1", spec="a")], confidence=0.7),
    )
    t = svc.get(task_id)
    svc.spawn_build_dag(t)
    svc._task_repo.save(t)  # noqa: SLF001


def _ev(task_id: str, kind: EventKind, seq: int, **payload) -> TaskEvent:
    return TaskEvent(task_id=task_id, seq=seq, kind=kind, payload=dict(payload))


# --- node lifecycle fold ----------------------------------------------------


def test_node_running_folds_pending_to_running():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    svc.on_event(_ev(t.id, EventKind.NODE_RUNNING, next_seq(svc._event_repo.latest_seq(t.id)), node_id="n1"))  # noqa: SLF001
    node = svc._find_node(svc.get(t.id), "n1")  # noqa: SLF001
    assert node.status is NodeStatus.RUNNING


def test_node_accepted_folds_running_to_done_with_pass():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    svc.claim_node(t.id, "n1", "bot-a")  # PENDING → RUNNING
    svc.on_event(_ev(t.id, EventKind.NODE_ACCEPTED, next_seq(svc._event_repo.latest_seq(t.id)), node_id="n1", verifier="bot-a"))  # noqa: SLF001
    node = svc._find_node(svc.get(t.id), "n1")  # noqa: SLF001
    assert node.status is NodeStatus.DONE
    assert node.properties.get("acceptance_result") == "pass"


def test_node_rejected_folds_running_to_failed():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    svc.claim_node(t.id, "n1", "bot-a")
    svc.on_event(_ev(t.id, EventKind.NODE_REJECTED, next_seq(svc._event_repo.latest_seq(t.id)), node_id="n1", verifier="bot-a", reason="nope"))  # noqa: SLF001
    node = svc._find_node(svc.get(t.id), "n1")  # noqa: SLF001
    # acceptance-fail lands in FAILED (PARTIAL_FAILED removed, spec R9);
    # the pass/fail distinction rides on acceptance_result, not the enum.
    assert node.status is NodeStatus.FAILED
    assert node.properties.get("acceptance_result") == "fail"


def test_node_failed_folds_running_to_failed():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    svc.claim_node(t.id, "n1", "bot-a")
    svc.on_event(_ev(t.id, EventKind.NODE_FAILED, next_seq(svc._event_repo.latest_seq(t.id)), node_id="n1", verifier="bot-a", reason="boom"))  # noqa: SLF001
    assert svc._find_node(svc.get(t.id), "n1").status is NodeStatus.FAILED  # noqa: SLF001


def test_execution_attempted_appends_record():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    svc.on_event(
        _ev(
            t.id,
            EventKind.EXECUTION_ATTEMPTED,
            next_seq(svc._event_repo.latest_seq(t.id)),  # noqa: SLF001
            node_id="n1",
            executor_id="bot-x",
            round=2,
            outcome="partial",
        )
    )
    recs = svc._find_node(svc.get(t.id), "n1").attempted_executors  # noqa: SLF001
    assert len(recs) == 1
    assert recs[0].executor_id == "bot-x"
    assert recs[0].round == 2


def test_loop_rerouted_bumps_loop_round():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    base = t.loop_round
    svc.on_event(_ev(t.id, EventKind.LOOP_REROUTED, next_seq(svc._event_repo.latest_seq(t.id)), node_id="n1", new_route="C5"))  # noqa: SLF001
    after = svc.get(t.id)
    assert after.loop_round == base + 1
    assert after.execution_graph.loop_round == base + 1


# --- goal verdict split -----------------------------------------------------


def test_goal_verified_done_with_verified_graph():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    # Goal verdict fires from REVIEWING (REVIEWING → DONE is the legal edge).
    t2 = svc.get(t.id)
    t2.status = TaskStatus.REVIEWING
    t2.execution_graph.root_phase = TaskStatus.REVIEWING
    svc._task_repo.save(t2)  # noqa: SLF001
    svc.on_event(_ev(t.id, EventKind.GOAL_VERIFIED, next_seq(svc._event_repo.latest_seq(t.id)), verifier="bot", verdict="pass", summary="ok"))  # noqa: SLF001
    final = svc.get(t.id)
    assert final.status is TaskStatus.DONE
    from agentclaw.community.core.task.domain.models import GraphStatus

    assert final.execution_graph.graph_status is GraphStatus.VERIFIED


def test_goal_rejected_pre_bbs_loops_gap():
    # v2 三终止(O-P2/§13):BBS 前 goal-FAIL → 回 gap(REVIEWING→EXECUTING,重跑 loop),
    # 非终态、非 AWAITING_HUMAN_ACCEPT。限轮次由 scheduler 守,超限 force-hang。
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    t2 = svc.get(t.id)
    t2.status = TaskStatus.REVIEWING
    t2.execution_graph.root_phase = TaskStatus.REVIEWING
    svc._task_repo.save(t2)  # noqa: SLF001
    svc.on_event(_ev(t.id, EventKind.GOAL_REJECTED, next_seq(svc._event_repo.latest_seq(t.id)), verifier="bot", verdict="fail", reason="nope", run_mode="single_bot"))  # noqa: SLF001
    final = svc.get(t.id)
    from agentclaw.community.core.task.domain.models import GraphStatus

    assert final.status is TaskStatus.EXECUTING  # 回 gap 重跑
    assert final.execution_graph.graph_status is GraphStatus.ON_PLAZA


def test_goal_rejected_post_bbs_failed_terminal():
    # v2 三终止(O-P2/§13):BBS 后 goal-FAIL → FAILED 终态(不回环/不再上升)。
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    t2 = svc.get(t.id)
    t2.status = TaskStatus.REVIEWING
    t2.execution_graph.root_phase = TaskStatus.REVIEWING
    svc._task_repo.save(t2)  # noqa: SLF001
    svc.on_event(_ev(t.id, EventKind.GOAL_REJECTED, next_seq(svc._event_repo.latest_seq(t.id)), verifier="bbs", verdict="fail", reason="plaza stuck", run_mode="bbs"))  # noqa: SLF001
    final = svc.get(t.id)
    assert final.status is TaskStatus.FAILED  # BBS 后终态


# --- cancel / envelope ----------------------------------------------------


def test_cancelled_event_folds_to_cancelled():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    svc.on_event(_ev(t.id, EventKind.CANCELLED, next_seq(svc._event_repo.latest_seq(t.id)), by="user", reason="abort"))  # noqa: SLF001
    assert svc.get(t.id).status is TaskStatus.CANCELLED


def test_on_event_accepts_dict_envelope():
    svc = _service()
    t = svc.create(title="t")
    _planned_with_dag(svc, t.id)
    envelope = {
        "task_id": t.id,
        "kind": EventKind.NODE_RUNNING.value,
        "seq": next_seq(svc._event_repo.latest_seq(t.id)),  # noqa: SLF001
        "payload": {"node_id": "n1"},
    }
    svc.on_event(envelope)
    assert svc._find_node(svc.get(t.id), "n1").status is NodeStatus.RUNNING  # noqa: SLF001


def test_on_event_unknown_task_returns_none():
    svc = _service()
    assert svc.on_event(_ev("ghost", EventKind.NODE_RUNNING, 1, node_id="n1")) is None