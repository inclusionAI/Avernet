"""Graph/Node legal-transition tables + guard helpers.

The only authority on which state move is legal. ``_apply_event`` consults
``require_graph_transition`` / ``require_node_transition`` before writing any
state change; an illegal move raises ``IllegalTransitionError`` and the event
is rejected (state stays put).

``GraphStatus`` is the single runtime task status (lives on the execution
graph). Terminals: ``DONE`` / ``CANCELLED`` / ``FAILED``. Hang = ``HUMAN_REQUIRED``
(stuck node ``NodeStatus.HUNG``); unrecoverable → ``FAILED``. Pre-BBS goal-FAIL
loops back to ``RUNNING`` (回 gap); post-BBS (``BBS_ACTIVE``) goal-FAIL →
``FAILED`` terminal (不回环). ``RUNNING`` → ``RUNNING`` is the loop_round++ self-edge.

Node terminal: ``SKIPPED`` only. ``DONE`` is idempotent (self-edge) but not
terminal. ``HUNG`` / ``FAILED`` may resume to ``RUNNING``. Acceptance-fail and
execution-fail both land in ``FAILED`` (distinguished by
``Node.properties['acceptance_result']``, not the enum).
"""
from __future__ import annotations

from .models import GraphStatus, NodeStatus


class IllegalTransitionError(ValueError):
    """Raised when a requested state move is not in the legal transition table."""


# --- graph (task runtime) transitions ---------------------------------------

TERMINAL_GRAPH_STATUSES: frozenset[GraphStatus] = frozenset(
    {GraphStatus.DONE, GraphStatus.CANCELLED, GraphStatus.FAILED}
)

_BASE_GRAPH_TRANSITIONS: dict[GraphStatus, frozenset[GraphStatus]] = {
    GraphStatus.DRAFTING: frozenset({GraphStatus.DEFINED}),
    GraphStatus.DEFINED: frozenset({GraphStatus.RUNNING}),
    GraphStatus.RUNNING: frozenset(
        {GraphStatus.HUMAN_REQUIRED, GraphStatus.REVIEWING, GraphStatus.RUNNING}
    ),
    GraphStatus.HUMAN_REQUIRED: frozenset({GraphStatus.BBS_ACTIVE, GraphStatus.FAILED}),
    GraphStatus.BBS_ACTIVE: frozenset({GraphStatus.DONE, GraphStatus.FAILED}),
    GraphStatus.REVIEWING: frozenset({GraphStatus.DONE, GraphStatus.RUNNING}),
    GraphStatus.DONE: frozenset(),
    GraphStatus.CANCELLED: frozenset(),
    GraphStatus.FAILED: frozenset(),
}

# Any non-terminal can also yield to CANCELLED.
GRAPH_TRANSITIONS: dict[GraphStatus, frozenset[GraphStatus]] = {
    src: outgoing | {GraphStatus.CANCELLED}
    if src not in TERMINAL_GRAPH_STATUSES
    else outgoing
    for src, outgoing in _BASE_GRAPH_TRANSITIONS.items()
}


# --- node transitions -------------------------------------------------------

TERMINAL_NODE_STATUSES: frozenset[NodeStatus] = frozenset({NodeStatus.SKIPPED})

NODE_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED, NodeStatus.HUNG}),
    NodeStatus.RUNNING: frozenset({NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.HUNG}),
    NodeStatus.FAILED: frozenset({NodeStatus.RUNNING, NodeStatus.DONE}),
    NodeStatus.HUNG: frozenset({NodeStatus.RUNNING}),
    NodeStatus.DONE: frozenset({NodeStatus.DONE}),
    NodeStatus.SKIPPED: frozenset(),
}


# --- guard helpers ----------------------------------------------------------

def can_graph_transition(current: GraphStatus, target: GraphStatus) -> bool:
    return target in GRAPH_TRANSITIONS.get(current, frozenset())


def require_graph_transition(current: GraphStatus, target: GraphStatus) -> None:
    if not can_graph_transition(current, target):
        raise IllegalTransitionError(
            f"illegal graph transition: {current.value} -> {target.value}"
        )


def can_node_transition(current: NodeStatus, target: NodeStatus) -> bool:
    return target in NODE_TRANSITIONS.get(current, frozenset())


def require_node_transition(current: NodeStatus, target: NodeStatus) -> None:
    if not can_node_transition(current, target):
        raise IllegalTransitionError(
            f"illegal node transition: {current.value} -> {target.value}"
        )