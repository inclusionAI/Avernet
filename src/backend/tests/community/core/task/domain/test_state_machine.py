"""TDD for task/node state machine (Phase 0.2; spec §2/§3.3 realignment).

Legal transition tables are the invariant guard TaskService._apply_event uses
before writing any state. 7 task states (DRAFTING/DEFINED/EXECUTING/REVIEWING/
DONE/CANCELLED/FAILED), 6 node states (PENDING/RUNNING/DONE/FAILED/SKIPPED/
HUMAN_REQUIRED). PARTIAL_FAILED removed; task-level HUNG removed.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import NodeStatus, TaskStatus
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
    NODE_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_NODE_STATUSES,
    TERMINAL_TASK_STATUSES,
    can_node_transition,
    can_task_transition,
    require_node_transition,
    require_task_transition,
)


# --- task transitions -------------------------------------------------------

def test_task_transitions_legal_forward_path():
    # DRAFTING → DEFINED → EXECUTING → REVIEWING → DONE (spec §2.2)
    assert TaskStatus.DEFINED in TASK_TRANSITIONS[TaskStatus.DRAFTING]
    assert TaskStatus.EXECUTING in TASK_TRANSITIONS[TaskStatus.DEFINED]
    assert TaskStatus.REVIEWING in TASK_TRANSITIONS[TaskStatus.EXECUTING]
    assert TaskStatus.DONE in TASK_TRANSITIONS[TaskStatus.REVIEWING]


def test_task_transitions_rework_and_loop_and_failed():
    # REVIEWING → EXECUTING is the rework loop (acceptance rejected → replan)
    assert TaskStatus.EXECUTING in TASK_TRANSITIONS[TaskStatus.REVIEWING]
    # EXECUTING → EXECUTING is loop_round++ self-edge
    assert TaskStatus.EXECUTING in TASK_TRANSITIONS[TaskStatus.EXECUTING]
    # EXECUTING → FAILED is the unrecoverable termination (spec R4)
    assert TaskStatus.FAILED in TASK_TRANSITIONS[TaskStatus.EXECUTING]


def test_task_transitions_cancel_from_any_non_terminal():
    for src in TaskStatus:
        if src in TERMINAL_TASK_STATUSES:
            continue
        assert TaskStatus.CANCELLED in TASK_TRANSITIONS[src]
    # task-level HUNG is gone — no non-terminal yields to HUNG (enum has no HUNG)
    assert not hasattr(TaskStatus, "HUNG")


def test_task_transitions_illegal():
    assert TaskStatus.EXECUTING not in TASK_TRANSITIONS[TaskStatus.DONE]
    assert TaskStatus.EXECUTING not in TASK_TRANSITIONS[TaskStatus.FAILED]
    assert TaskStatus.DEFINED not in TASK_TRANSITIONS[TaskStatus.DONE]
    # DRAFTING may NOT jump straight to EXECUTING (must go via DEFINED)
    assert TaskStatus.EXECUTING not in TASK_TRANSITIONS[TaskStatus.DRAFTING]
    assert len(TASK_TRANSITIONS[TaskStatus.DONE]) == 0
    assert len(TASK_TRANSITIONS[TaskStatus.CANCELLED]) == 0
    assert len(TASK_TRANSITIONS[TaskStatus.FAILED]) == 0


def test_terminal_task_statuses():
    assert TERMINAL_TASK_STATUSES == frozenset(
        {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED}
    )


# --- node transitions -------------------------------------------------------

def test_node_transitions_legal_running_to_outcomes():
    running_out = NODE_TRANSITIONS[NodeStatus.RUNNING]
    assert NodeStatus.DONE in running_out
    assert NodeStatus.FAILED in running_out  # acceptance-fail + execution-fail unified
    assert NodeStatus.HUMAN_REQUIRED in running_out
    assert not hasattr(NodeStatus, "PARTIAL_FAILED")  # removed (spec R9)


def test_node_transitions_pending():
    out = NODE_TRANSITIONS[NodeStatus.PENDING]
    assert NodeStatus.RUNNING in out
    assert NodeStatus.SKIPPED in out


def test_node_transitions_retry_back_to_running():
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.FAILED]
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.HUMAN_REQUIRED]
    # FAILED → DONE (acceptance-pass兜底, spec R11) also legal
    assert NodeStatus.DONE in NODE_TRANSITIONS[NodeStatus.FAILED]


def test_node_done_idempotent_and_terminal():
    assert NodeStatus.DONE in NODE_TRANSITIONS[NodeStatus.DONE]
    assert NodeStatus.SKIPPED not in NODE_TRANSITIONS[NodeStatus.DONE]
    assert len(NODE_TRANSITIONS[NodeStatus.SKIPPED]) == 0


def test_terminal_node_statuses():
    assert TERMINAL_NODE_STATUSES == frozenset({NodeStatus.SKIPPED})


def test_node_illegal():
    assert NodeStatus.RUNNING not in NODE_TRANSITIONS[NodeStatus.DONE]
    assert NodeStatus.PENDING not in NODE_TRANSITIONS[NodeStatus.SKIPPED]


# --- guard helpers ----------------------------------------------------------

def test_can_task_transition_true_false():
    assert can_task_transition(TaskStatus.DRAFTING, TaskStatus.DEFINED) is True
    assert can_task_transition(TaskStatus.DONE, TaskStatus.EXECUTING) is False
    assert can_task_transition(TaskStatus.EXECUTING, TaskStatus.FAILED) is True


def test_require_task_transition_legal_no_raise():
    require_task_transition(TaskStatus.DEFINED, TaskStatus.EXECUTING)  # no raise
    require_task_transition(TaskStatus.REVIEWING, TaskStatus.EXECUTING)  # rework
    require_task_transition(TaskStatus.EXECUTING, TaskStatus.FAILED)  # R4


def test_require_task_transition_illegal_raises():
    with pytest.raises(IllegalTransitionError):
        require_task_transition(TaskStatus.DONE, TaskStatus.EXECUTING)
    with pytest.raises(IllegalTransitionError):
        require_task_transition(TaskStatus.CANCELLED, TaskStatus.EXECUTING)
    with pytest.raises(IllegalTransitionError):
        require_task_transition(TaskStatus.DRAFTING, TaskStatus.EXECUTING)  # must via DEFINED


def test_can_require_node_transition():
    assert can_node_transition(NodeStatus.PENDING, NodeStatus.RUNNING) is True
    assert can_node_transition(NodeStatus.DONE, NodeStatus.RUNNING) is False
    require_node_transition(NodeStatus.RUNNING, NodeStatus.FAILED)  # no raise
    with pytest.raises(IllegalTransitionError):
        require_node_transition(NodeStatus.SKIPPED, NodeStatus.RUNNING)


def test_illegal_transition_error_is_value_error():
    assert issubclass(IllegalTransitionError, ValueError)
