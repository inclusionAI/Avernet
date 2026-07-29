"""TDD for task/node state machine (Phase 0.2).

Legal transition tables are the invariant guard TaskService._apply_event uses
before writing any state. Red first, then green via state_machine.py.
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
    assert TaskStatus.DISCUSSING in TASK_TRANSITIONS[TaskStatus.INTAKE]
    assert TaskStatus.PLANNED in TASK_TRANSITIONS[TaskStatus.INTAKE]
    assert TaskStatus.PLANNED in TASK_TRANSITIONS[TaskStatus.DISCUSSING]
    assert TaskStatus.EXECUTING in TASK_TRANSITIONS[TaskStatus.PLANNED]
    assert TaskStatus.VALIDATING in TASK_TRANSITIONS[TaskStatus.EXECUTING]
    assert TaskStatus.DELIVERED in TASK_TRANSITIONS[TaskStatus.VALIDATING]
    assert TaskStatus.EXECUTING in TASK_TRANSITIONS[TaskStatus.VALIDATING]  # LOOP reroute


def test_task_transitions_back_and_loop():
    assert TaskStatus.INTAKE in TASK_TRANSITIONS[TaskStatus.DISCUSSING]  # need more info
    assert TaskStatus.DISCUSSING in TASK_TRANSITIONS[TaskStatus.PLANNED]  # replan needs info
    assert TaskStatus.EXECUTING in TASK_TRANSITIONS[TaskStatus.EXECUTING]  # loop round++


def test_task_transitions_terminal_from_any_non_terminal():
    for src in TaskStatus:
        if src in TERMINAL_TASK_STATUSES:
            continue
        assert TaskStatus.CANCELLED in TASK_TRANSITIONS[src]
        assert TaskStatus.HUNG in TASK_TRANSITIONS[src]


def test_task_transitions_illegal():
    assert TaskStatus.EXECUTING not in TASK_TRANSITIONS[TaskStatus.DELIVERED]
    assert TaskStatus.PLANNED not in TASK_TRANSITIONS[TaskStatus.CANCELLED]
    assert len(TASK_TRANSITIONS[TaskStatus.DELIVERED]) == 0
    assert len(TASK_TRANSITIONS[TaskStatus.CANCELLED]) == 0
    assert len(TASK_TRANSITIONS[TaskStatus.HUNG]) == 0


def test_terminal_task_statuses():
    assert TERMINAL_TASK_STATUSES == frozenset(
        {TaskStatus.DELIVERED, TaskStatus.CANCELLED, TaskStatus.HUNG}
    )


# --- node transitions -------------------------------------------------------

def test_node_transitions_legal_running_to_outcomes():
    running_out = NODE_TRANSITIONS[NodeStatus.RUNNING]
    assert NodeStatus.DONE in running_out
    assert NodeStatus.PARTIAL_FAILED in running_out
    assert NodeStatus.FAILED in running_out
    assert NodeStatus.HUMAN_REQUIRED in running_out


def test_node_transitions_pending():
    out = NODE_TRANSITIONS[NodeStatus.PENDING]
    assert NodeStatus.RUNNING in out
    assert NodeStatus.SKIPPED in out


def test_node_transitions_retry_back_to_running():
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.PARTIAL_FAILED]
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.FAILED]
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.HUMAN_REQUIRED]


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
    assert can_task_transition(TaskStatus.INTAKE, TaskStatus.DISCUSSING) is True
    assert can_task_transition(TaskStatus.DELIVERED, TaskStatus.EXECUTING) is False


def test_require_task_transition_legal_no_raise():
    require_task_transition(TaskStatus.PLANNED, TaskStatus.EXECUTING)  # no raise
    require_task_transition(TaskStatus.VALIDATING, TaskStatus.EXECUTING)  # LOOP


def test_require_task_transition_illegal_raises():
    with pytest.raises(IllegalTransitionError):
        require_task_transition(TaskStatus.DELIVERED, TaskStatus.EXECUTING)
    with pytest.raises(IllegalTransitionError):
        require_task_transition(TaskStatus.CANCELLED, TaskStatus.EXECUTING)


def test_can_require_node_transition():
    assert can_node_transition(NodeStatus.PENDING, NodeStatus.RUNNING) is True
    assert can_node_transition(NodeStatus.DONE, NodeStatus.RUNNING) is False
    require_node_transition(NodeStatus.RUNNING, NodeStatus.DONE)  # no raise
    with pytest.raises(IllegalTransitionError):
        require_node_transition(NodeStatus.SKIPPED, NodeStatus.RUNNING)


def test_illegal_transition_error_is_value_error():
    assert issubclass(IllegalTransitionError, ValueError)