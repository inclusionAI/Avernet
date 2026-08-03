"""TDD for task domain events (Phase 0.3).

Events are the only input to TaskService._apply_event (event-sourced fold).
``seq`` is the single-writer watermark (TaskEventRepo.append assigns it).
``reported`` marks owner-bot SKILL 回投 events (ACCEPTANCE_* / GOAL_VERIFIED)
so the guard distinguishes system-driven vs bot-reported state moves.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.events import (
    Cancelled,
    EventKind,
    ExecutionAttempted,
    GoalRejected,
    GoalVerified,
    Hung,
    IllegalEventError,
    LoopRerouted,
    NodeAccepted,
    NodeDispatched,
    NodeFailed,
    NodeRejected,
    NodeRunning,
    TaskClarified,
    TASK_CREATED_KIND,
    TaskCreated,
    TaskEvent,
    is_reported_kind,
    next_seq,
)
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    AttemptTrigger,
    NodeStatus,
    RunMode,
    RouteClass,
)


# --- event kind enum --------------------------------------------------------

def test_event_kind_has_core_round_trip_kinds():
    assert EventKind.TASK_CREATED.value == "task.created"
    assert EventKind.TASK_CLARIFIED.value == "task.clarified"
    assert EventKind.NODE_DISPATCHED.value == "node.dispatched"
    assert EventKind.NODE_RUNNING.value == "node.running"
    assert EventKind.NODE_ACCEPTED.value == "node.accepted"
    assert EventKind.NODE_REJECTED.value == "node.rejected"
    assert EventKind.NODE_FAILED.value == "node.failed"
    assert EventKind.GOAL_VERIFIED.value == "goal.verified"
    assert EventKind.GOAL_REJECTED.value == "goal.rejected"
    assert EventKind.LOOP_REROUTED.value == "loop.rerouted"
    assert EventKind.EXECUTION_ATTEMPTED.value == "execution.attempted"
    assert EventKind.CANCELLED.value == "task.cancelled"
    assert EventKind.HUNG.value == "task.hung"


# --- base event -------------------------------------------------------------

def test_task_event_base_fields():
    ev = TaskEvent(task_id="t1", seq=5, kind=EventKind.NODE_RUNNING)
    assert ev.task_id == "t1"
    assert ev.seq == 5
    assert ev.kind is EventKind.NODE_RUNNING
    assert ev.reported is False
    assert ev.payload == {}


def test_task_event_seq_must_be_non_negative():
    with pytest.raises(IllegalEventError):
        TaskEvent(task_id="t1", seq=-1, kind=EventKind.TASK_CREATED)


# --- next_seq invariant (single-writer watermark) ---------------------------

def test_next_seq_from_zero():
    assert next_seq(None) == 1


def test_next_seq_increments():
    assert next_seq(0) == 1
    assert next_seq(5) == 6
    assert next_seq(99) == 100


def test_next_seq_rejects_negative_latest():
    with pytest.raises(IllegalEventError):
        next_seq(-1)


# --- concrete events shape --------------------------------------------------

def test_task_created_event():
    ev = TaskCreated(task_id="t1", seq=1, title="fix PR", source="im")
    assert ev.kind is EventKind.TASK_CREATED
    assert ev.kind is TASK_CREATED_KIND
    assert ev.title == "fix PR"
    assert ev.source == "im"


def test_task_clarified_merges_payload_and_confirmed_flag():
    ev = TaskClarified(task_id="t1", seq=2, patch={"goal": "new objective"}, confirmed=True)
    assert ev.payload["patch"] == {"goal": "new objective"}
    assert ev.payload["confirmed"] is True


def test_task_clarified_default_confirmed_false():
    ev = TaskClarified(task_id="t1", seq=3, patch={"x": 1})
    assert ev.payload["confirmed"] is False


def test_node_dispatched_carries_route():
    ev = NodeDispatched(
        task_id="t1", seq=4, node_id="n1", route_class=RouteClass.C3, run_mode=RunMode.COOP_GROUP
    )
    assert ev.node_id == "n1"
    assert ev.payload["route_class"] is RouteClass.C3
    assert ev.payload["run_mode"] is RunMode.COOP_GROUP


def test_node_status_change_events():
    running = NodeRunning(task_id="t1", seq=5, node_id="n1", from_status=NodeStatus.PENDING)
    assert running.payload["from_status"] is NodeStatus.PENDING
    assert running.node_id == "n1"


# --- reported (owner-bot SKILL 回投) events ---------------------------------

def test_node_accepted_is_reported():
    ev = NodeAccepted(task_id="t1", seq=6, node_id="n1", verifier="bot-x")
    assert ev.reported is True
    assert ev.payload["verifier"] == "bot-x"
    assert ev.kind is EventKind.NODE_ACCEPTED


def test_node_rejected_is_reported_carries_reason():
    ev = NodeRejected(task_id="t1", seq=7, node_id="n1", verifier="bot-x", reason="tests fail")
    assert ev.reported is True
    assert ev.payload["reason"] == "tests fail"


def test_node_failed_is_reported():
    ev = NodeFailed(task_id="t1", seq=8, node_id="n1", verifier="bot-x", reason="crash")
    assert ev.reported is True


def test_goal_verified_is_reported_carrying_verdict():
    ev = GoalVerified(task_id="t1", seq=9, verifier="owner-bot", verdict="PASS", summary="all green")
    assert ev.reported is True
    assert ev.payload["verdict"] == "PASS"
    assert ev.payload["summary"] == "all green"


def test_goal_rejected_is_reported():
    ev = GoalRejected(task_id="t1", seq=10, verifier="owner-bot", verdict="FAIL")
    assert ev.reported is True
    assert ev.payload["verdict"] == "FAIL"


# --- is_reported_kind classifier --------------------------------------------

def test_is_reported_kind_true_only_for_bot_reported():
    assert is_reported_kind(EventKind.NODE_ACCEPTED) is True
    assert is_reported_kind(EventKind.NODE_REJECTED) is True
    assert is_reported_kind(EventKind.NODE_FAILED) is True
    assert is_reported_kind(EventKind.GOAL_VERIFIED) is True
    assert is_reported_kind(EventKind.GOAL_REJECTED) is True
    assert is_reported_kind(EventKind.TASK_CREATED) is False
    assert is_reported_kind(EventKind.NODE_DISPATCHED) is False
    assert is_reported_kind(EventKind.LOOP_REROUTED) is False


def test_reported_events_carry_reported_flag_consistent_with_kind():
    for kind, ctor in [
        (EventKind.NODE_ACCEPTED, lambda s: NodeAccepted(task_id="t1", seq=s, node_id="n1", verifier="bot")),
        (EventKind.NODE_REJECTED, lambda s: NodeRejected(task_id="t1", seq=s, node_id="n1", verifier="bot")),
        (EventKind.NODE_FAILED, lambda s: NodeFailed(task_id="t1", seq=s, node_id="n1", verifier="bot")),
        (EventKind.GOAL_VERIFIED, lambda s: GoalVerified(task_id="t1", seq=s, verifier="bot", verdict="PASS")),
        (EventKind.GOAL_REJECTED, lambda s: GoalRejected(task_id="t1", seq=s, verifier="bot", verdict="FAIL")),
    ]:
        ev = ctor(1)
        assert ev.reported is True and ev.kind is kind


# --- non-reported system events ---------------------------------------------

def test_loop_rerouted_not_reported():
    ev = LoopRerouted(task_id="t1", seq=11, node_id="n1", new_route=RouteClass.C5)
    assert ev.reported is False
    assert ev.payload["new_route"] is RouteClass.C5


def test_execution_attempted_carries_attempt_record_fields():
    ev = ExecutionAttempted(
        task_id="t1",
        seq=12,
        node_id="n1",
        executor_id="b1",
        paradigm=RunMode.SINGLE_BOT,
        round=1,
        route_class=RouteClass.C3,
        trigger=AttemptTrigger.ROUTED,
        outcome=AttemptOutcome.PASS,
    )
    assert ev.reported is False
    assert ev.payload["executor_id"] == "b1"
    assert ev.payload["outcome"] is AttemptOutcome.PASS


def test_cancelled_and_hung_not_reported():
    c = Cancelled(task_id="t1", seq=13, by="user", reason="cancel")
    h = Hung(task_id="t1", seq=14, reason="awaiting human")
    assert c.reported is False
    assert c.payload["by"] == "user"
    assert h.reported is False
    assert h.payload["reason"] == "awaiting human"