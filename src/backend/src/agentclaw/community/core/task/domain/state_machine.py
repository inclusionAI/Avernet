"""Task/Node legal-transition tables + guard helpers (Phase 0.2).

This module is the *only* authority on which state move is legal. TaskService
``_apply_event`` consults ``require_task_transition`` / ``require_node_transition``
as the guard before writing any state change; an illegal move raises
``IllegalTransitionError`` and the event is rejected (state stays put).

Notes (plan §1.1-§1.2, §2.1):
- Task terminals: DELIVERED / CANCELLED / HUNG — no outgoing edges.
- Any non-terminal Task can yield to CANCELLED or HUNG (user cancel / hung).
- VALIDATING → EXECUTING is the LOOP reroute (accept FAIL → replan).
- EXECUTING → EXECUTING is loop_round++ self-edge (not a state change, but
  legal so the guard doesn't reject a tick that bumps rounds).
- Node terminal: SKIPPED only. DONE is idempotent (self-edge) but not terminal
  (a DONE node can still be re-opened by a replan? No — keep it terminal-ish:
  only self-edge allowed; re-open goes via parent graph re-issue, not here).
- HUMAN_REQUIRED / PARTIAL_FAILED / FAILED all retry back to RUNNING (owner-bot
  SKILL 回投 FAIL after accept, or human adjusts then resumes).
"""
from __future__ import annotations

from .models import NodeStatus, TaskStatus


class IllegalTransitionError(ValueError):
    """Raised when a requested state move is not in the legal transition table."""


# --- task transitions -------------------------------------------------------

TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.DELIVERED, TaskStatus.CANCELLED, TaskStatus.HUNG}
)

_BASE_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.INTAKE: frozenset(
        {TaskStatus.DISCUSSING, TaskStatus.PLANNED}
    ),
    TaskStatus.DISCUSSING: frozenset(
        {TaskStatus.INTAKE, TaskStatus.PLANNED}
    ),
    TaskStatus.PLANNED: frozenset(
        {TaskStatus.EXECUTING, TaskStatus.DISCUSSING}
    ),
    TaskStatus.EXECUTING: frozenset(
        {TaskStatus.VALIDATING, TaskStatus.EXECUTING}
    ),
    TaskStatus.VALIDATING: frozenset(
        {TaskStatus.DELIVERED, TaskStatus.EXECUTING}
    ),
    TaskStatus.DELIVERED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.HUNG: frozenset(),
}

# Any non-terminal can also yield to CANCELLED / HUNG.
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    src: outgoing | {TaskStatus.CANCELLED, TaskStatus.HUNG}
    if src not in TERMINAL_TASK_STATUSES
    else outgoing
    for src, outgoing in _BASE_TASK_TRANSITIONS.items()
}


# --- node transitions -------------------------------------------------------

TERMINAL_NODE_STATUSES: frozenset[NodeStatus] = frozenset({NodeStatus.SKIPPED})

NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED}),
    NodeStatus.RUNNING: frozenset(
        {NodeStatus.DONE, NodeStatus.PARTIAL_FAILED, NodeStatus.FAILED, NodeStatus.HUMAN_REQUIRED}
    ),
    NodeStatus.PARTIAL_FAILED: frozenset({NodeStatus.RUNNING}),
    NodeStatus.FAILED: frozenset({NodeStatus.RUNNING}),
    NodeStatus.HUMAN_REQUIRED: frozenset({NodeStatus.RUNNING}),
    NodeStatus.DONE: frozenset({NodeStatus.DONE}),
    NodeStatus.SKIPPED: frozenset(),
}


# --- guard helpers ----------------------------------------------------------

def can_task_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in TASK_TRANSITIONS.get(current, frozenset())


def can_node_transition(current: NodeStatus, target: NodeStatus) -> bool:
    return target in NODE_TRANSITIONS.get(current, frozenset())


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if not can_task_transition(current, target):
        raise IllegalTransitionError(
            f"illegal task transition: {current.value} -> {target.value}"
        )


def require_node_transition(current: NodeStatus, target: NodeStatus) -> None:
    if not can_node_transition(current, target):
        raise IllegalTransitionError(
            f"illegal node transition: {current.value} -> {target.value}"
        )