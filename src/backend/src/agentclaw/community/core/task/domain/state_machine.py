"""Task/Node legal-transition tables + guard helpers (Phase 0.2).

This module is the *only* authority on which state move is legal. TaskService
``_apply_event`` consults ``require_task_transition`` / ``require_node_transition``
as the guard before writing any state change; an illegal move raises
``IllegalTransitionError`` and the event is rejected (state stays put).

Notes (spec §2, §3.3):
- Task terminals: DONE / CANCELLED / FAILED — no outgoing edges.
- Any non-terminal Task can yield to CANCELLED (user cancel). Task-level HUNG is
  gone — "被 hung 住 / 上升等人工" is node-level HUMAN_REQUIRED (task stays
  EXECUTING); unrecoverable blockage → FAILED.
- REVIEWING → EXECUTING is the rework loop (acceptance rejected → replan).
- EXECUTING → FAILED is the unrecoverable termination (spec R4: atomic
  termination OR node MAX_ATTEMPTS exhausted with no reroute/split room).
- EXECUTING → EXECUTING is loop_round++ self-edge (not a state change, but
  legal so the guard doesn't reject a tick that bumps rounds).
- Node terminal: SKIPPED only. DONE is idempotent (self-edge) but not terminal.
- HUMAN_REQUIRED / FAILED retry back to RUNNING (owner-bot SKILL 回投 FAIL
  after accept, or human adjusts then resumes). PARTIAL_FAILED removed;
  acceptance-fail and execution-fail both land in FAILED (spec R9), distinguished
  by ``Node.properties['acceptance_result']`` / failure kind, not the enum.
"""
from __future__ import annotations

from .models import NodeStatus, TaskStatus


class IllegalTransitionError(ValueError):
    """Raised when a requested state move is not in the legal transition table."""


# --- task transitions -------------------------------------------------------

TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.FAILED}
)

_BASE_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFTING: frozenset({TaskStatus.DEFINED}),
    TaskStatus.DEFINED: frozenset({TaskStatus.EXECUTING}),
    TaskStatus.EXECUTING: frozenset(
        {TaskStatus.REVIEWING, TaskStatus.EXECUTING, TaskStatus.FAILED}
    ),
    TaskStatus.REVIEWING: frozenset({TaskStatus.DONE, TaskStatus.EXECUTING}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}

# Any non-terminal can also yield to CANCELLED (task-level HUNG is gone).
TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    src: outgoing | {TaskStatus.CANCELLED}
    if src not in TERMINAL_TASK_STATUSES
    else outgoing
    for src, outgoing in _BASE_TASK_TRANSITIONS.items()
}


# --- node transitions -------------------------------------------------------

TERMINAL_NODE_STATUSES: frozenset[NodeStatus] = frozenset({NodeStatus.SKIPPED})

NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED}),
    NodeStatus.RUNNING: frozenset(
        {NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.HUMAN_REQUIRED}
    ),
    NodeStatus.FAILED: frozenset({NodeStatus.RUNNING, NodeStatus.DONE}),
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