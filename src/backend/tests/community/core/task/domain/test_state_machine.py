"""TDD for graph/node state machine (spec §2/§3.3).

Legal transition tables are the invariant guard ``_apply_event`` uses before
writing any state. ``GraphStatus`` is the single runtime task status
(DRAFTING/DEFINED/RUNNING/HUMAN_REQUIRED/BBS_ACTIVE/REVIEWING/DONE/FAILED/
CANCELLED); node statuses PENDING/RUNNING/DONE/FAILED/SKIPPED/HUNG.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.models import NodeStatus, GraphStatus
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
    NODE_TRANSITIONS,
    GRAPH_TRANSITIONS,
    TERMINAL_NODE_STATUSES,
    TERMINAL_GRAPH_STATUSES,
    can_node_transition,
    can_graph_transition,
    require_node_transition,
    require_graph_transition,
)


# --- graph (task runtime) transitions --------------------------------------

def test_graph_transitions_legal_forward_path():
    # DRAFTING → DEFINED → RUNNING → REVIEWING → DONE
    assert GraphStatus.DEFINED in GRAPH_TRANSITIONS[GraphStatus.DRAFTING]
    assert GraphStatus.RUNNING in GRAPH_TRANSITIONS[GraphStatus.DEFINED]
    assert GraphStatus.REVIEWING in GRAPH_TRANSITIONS[GraphStatus.RUNNING]
    assert GraphStatus.DONE in GRAPH_TRANSITIONS[GraphStatus.REVIEWING]


def test_graph_transitions_hang_bbs_and_loop():
    # RUNNING → HUMAN_REQUIRED (mark hang);HUMAN_REQUIRED → BBS_ACTIVE (人确认升) / → FAILED (人不升)
    assert GraphStatus.HUMAN_REQUIRED in GRAPH_TRANSITIONS[GraphStatus.RUNNING]
    assert GraphStatus.BBS_ACTIVE in GRAPH_TRANSITIONS[GraphStatus.HUMAN_REQUIRED]
    assert GraphStatus.FAILED in GRAPH_TRANSITIONS[GraphStatus.HUMAN_REQUIRED]
    # BBS_ACTIVE → DONE / FAILED (post-BBS 终验,fail 终态不回环)
    assert GraphStatus.DONE in GRAPH_TRANSITIONS[GraphStatus.BBS_ACTIVE]
    assert GraphStatus.FAILED in GRAPH_TRANSITIONS[GraphStatus.BBS_ACTIVE]
    # REVIEWING → RUNNING (回 gap 重做)+ RUNNING → RUNNING (loop_round++ 自边)
    assert GraphStatus.RUNNING in GRAPH_TRANSITIONS[GraphStatus.REVIEWING]
    assert GraphStatus.RUNNING in GRAPH_TRANSITIONS[GraphStatus.RUNNING]


def test_graph_transitions_cancel_from_any_non_terminal():
    for src in GraphStatus:
        if src in TERMINAL_GRAPH_STATUSES:
            continue
        assert GraphStatus.CANCELLED in GRAPH_TRANSITIONS[src]
    assert not hasattr(GraphStatus, "HUNG")  # HUNG 是节点态,非图态


def test_graph_transitions_illegal():
    assert GraphStatus.RUNNING not in GRAPH_TRANSITIONS[GraphStatus.DONE]
    assert GraphStatus.RUNNING not in GRAPH_TRANSITIONS[GraphStatus.FAILED]
    assert GraphStatus.DEFINED not in GRAPH_TRANSITIONS[GraphStatus.DONE]
    # DRAFTING may NOT jump straight to RUNNING (must go via DEFINED)
    assert GraphStatus.RUNNING not in GRAPH_TRANSITIONS[GraphStatus.DRAFTING]
    # RUNNING 不直接到 FAILED(须经 HUMAN_REQUIRED 人确认不升,或 BBS_ACTIVE 终验失败)
    assert GraphStatus.FAILED not in GRAPH_TRANSITIONS[GraphStatus.RUNNING]
    assert len(GRAPH_TRANSITIONS[GraphStatus.DONE]) == 0
    assert len(GRAPH_TRANSITIONS[GraphStatus.CANCELLED]) == 0
    assert len(GRAPH_TRANSITIONS[GraphStatus.FAILED]) == 0


def test_terminal_graph_statuses():
    assert TERMINAL_GRAPH_STATUSES == frozenset(
        {GraphStatus.DONE, GraphStatus.CANCELLED, GraphStatus.FAILED}
    )


# --- node transitions -------------------------------------------------------

def test_node_transitions_legal_running_to_outcomes():
    running_out = NODE_TRANSITIONS[NodeStatus.RUNNING]
    assert NodeStatus.DONE in running_out
    assert NodeStatus.FAILED in running_out  # acceptance-fail + execution-fail unified
    assert NodeStatus.HUNG in running_out
    assert not hasattr(NodeStatus, "PARTIAL_FAILED")  # removed (spec R9)


def test_node_transitions_pending():
    out = NODE_TRANSITIONS[NodeStatus.PENDING]
    assert NodeStatus.RUNNING in out
    assert NodeStatus.SKIPPED in out
    assert NodeStatus.HUNG in out  # PENDING 卡住(递归上限)→ HUNG


def test_node_transitions_retry_back_to_running():
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.FAILED]
    assert NodeStatus.RUNNING in NODE_TRANSITIONS[NodeStatus.HUNG]
    # FAILED → DONE (acceptance-pass 兜底, spec R11) also legal
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

def test_can_graph_transition_true_false():
    assert can_graph_transition(GraphStatus.DRAFTING, GraphStatus.DEFINED) is True
    assert can_graph_transition(GraphStatus.DONE, GraphStatus.RUNNING) is False
    assert can_graph_transition(GraphStatus.RUNNING, GraphStatus.FAILED) is False
    assert can_graph_transition(GraphStatus.HUMAN_REQUIRED, GraphStatus.FAILED) is True


def test_require_graph_transition_legal_no_raise():
    require_graph_transition(GraphStatus.DEFINED, GraphStatus.RUNNING)  # no raise
    require_graph_transition(GraphStatus.REVIEWING, GraphStatus.RUNNING)  # rework 回 gap
    require_graph_transition(GraphStatus.HUMAN_REQUIRED, GraphStatus.FAILED)  # 人不升 → FAILED
    require_graph_transition(GraphStatus.BBS_ACTIVE, GraphStatus.FAILED)  # post-BBS 终验失败


def test_require_graph_transition_illegal_raises():
    with pytest.raises(IllegalTransitionError):
        require_graph_transition(GraphStatus.DONE, GraphStatus.RUNNING)
    with pytest.raises(IllegalTransitionError):
        require_graph_transition(GraphStatus.CANCELLED, GraphStatus.RUNNING)
    with pytest.raises(IllegalTransitionError):
        require_graph_transition(GraphStatus.DRAFTING, GraphStatus.RUNNING)  # must via DEFINED
    with pytest.raises(IllegalTransitionError):
        require_graph_transition(GraphStatus.RUNNING, GraphStatus.FAILED)  # 须经 HUMAN_REQUIRED


def test_can_require_node_transition():
    assert can_node_transition(NodeStatus.PENDING, NodeStatus.RUNNING) is True
    assert can_node_transition(NodeStatus.DONE, NodeStatus.RUNNING) is False
    require_node_transition(NodeStatus.RUNNING, NodeStatus.FAILED)  # no raise
    with pytest.raises(IllegalTransitionError):
        require_node_transition(NodeStatus.SKIPPED, NodeStatus.RUNNING)


def test_illegal_transition_error_is_value_error():
    assert issubclass(IllegalTransitionError, ValueError)
