"""Domain models for task trajectory collection & root-cause analysis (REQ-1).

These tests pin the dataclass shapes and enum membership defined in
``core/task/task_trajectory/models.py`` before the implementation exists
(TDD red→green). The module is pure dataclasses/enum with no side effects.
"""
from __future__ import annotations

import dataclasses
import typing

from agentclaw.community.core.task.domain.models import NodeAction, Status
from agentclaw.community.core.task.task_trajectory.models import (
    DispatchCandidate,
    DispatchRationale,
    JoinDropped,
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryAnalysis,
    TrajectoryEvent,
)


# --- ReasonCatalog ----------------------------------------------------------

def test_reason_catalog_members_cover_spec_signals():
    # 通用失败分类 (10) + 派发侧 JOIN 丢因 (5) = 15
    expected = {
        # 通用失败信号 (§概述 + REQ-5 exec_error_origin)
        "execution_timeout",
        "underlying_interface_error",
        "dispatch_stuck",
        "hung",
        "plan_failure",
        "acceptance_failed",
        "parse_error",
        "transport_error",
        "terminal_invalid",
        "unclassified",
        # 派发侧 JOIN 丢因 (REQ-2/REQ-7)
        "join_dropped",
        "no_candidates",
        "score_below_threshold",
        "claim_mode_off",
        "catalog_miss",
    }
    assert {e.value for e in ReasonCatalog} == expected


# --- TrajectoryActionType --------------------------------------------------

def test_trajectory_action_type_has_exactly_seven_members():
    assert {e.value for e in TrajectoryActionType} == {
        "submit", "plan", "dispatch", "execute", "verify", "reset", "transition",
    }


def test_trajectory_action_type_is_separate_from_node_action():
    # Distinct enum class; no inheritance either way.
    assert TrajectoryActionType is not NodeAction
    assert not issubclass(TrajectoryActionType, NodeAction)
    assert not issubclass(NodeAction, TrajectoryActionType)
    # submit is new in TrajectoryActionType and absent from NodeAction.
    assert hasattr(TrajectoryActionType, "SUBMIT")
    assert not hasattr(NodeAction, "SUBMIT")
    # The value sets differ (NodeAction lacks submit) — not equal, not a subset.
    na_values = {e.value for e in NodeAction}
    tat_values = {e.value for e in TrajectoryActionType}
    assert tat_values != na_values
    assert tat_values.issuperset(na_values)  # 6 overlap + submit
    # Conversely, NodeAction is not a superset of TrajectoryActionType.
    assert not na_values.issuperset(tat_values)


# --- TrajectoryEvent (FLAT) -------------------------------------------------

def _event(**overrides) -> TrajectoryEvent:
    base = dict(
        task_id="t1",
        node_id="n1",
        action_type=TrajectoryActionType.EXECUTE,
        action_result="failed",
        attempt=2,
        gmt_create=1700000000_000,
        gmt_modify=1700000000_000,
    )
    base.update(overrides)
    return TrajectoryEvent(**base)


def test_trajectory_event_constructs_from_kwargs_with_none_defaults():
    ev = _event()
    assert ev.task_id == "t1"
    assert ev.node_id == "n1"
    assert ev.action_type is TrajectoryActionType.EXECUTE
    assert ev.action_result == "failed"
    assert ev.attempt == 2
    assert ev.gmt_create == 1700000000_000
    assert ev.gmt_modify == 1700000000_000
    # None defaults per spec (not-yet-computed / success / no-input).
    assert ev.action_input is None
    assert ev.status_from is None
    assert ev.status_to is None
    assert ev.error_type is None
    assert ev.error_msg is None
    assert ev.analysis is None


def test_trajectory_event_is_flat_no_nested_payload_rationale_phase():
    field_names = {f.name for f in dataclasses.fields(TrajectoryEvent)}
    assert "payload" not in field_names
    assert "rationale" not in field_names
    assert "phase" not in field_names
    # Required (non-optional) fields exist.
    required = {
        "task_id", "node_id", "action_type", "action_result",
        "attempt", "gmt_create", "gmt_modify",
    }
    assert required.issubset(field_names)
    # Required fields must NOT be Optional (T | None). ``from __future__``
    # stringises annotations, so resolve via get_type_hints.
    hints = typing.get_type_hints(TrajectoryEvent)
    for name in required:
        tp = hints[name]
        if typing.get_origin(tp) is not None:
            assert type(None) not in typing.get_args(tp), (
                f"{name!r} must be non-optional, got {tp!r}"
            )


def test_trajectory_event_error_type_accepts_reason_catalog():
    ev = _event(
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg="interface exploded",
    )
    assert ev.error_type is ReasonCatalog.UNDERLYING_INTERFACE_ERROR
    assert ev.error_msg == "interface exploded"


def test_trajectory_event_status_fields_accept_domain_status_enum():
    ev = _event(
        action_type=TrajectoryActionType.TRANSITION,
        action_result="success",
        status_from=Status.RUNNING,
        status_to=Status.DONE,
    )
    assert ev.status_from is Status.RUNNING
    assert ev.status_to is Status.DONE


# --- TaskTrajectory ---------------------------------------------------------

def test_task_trajectory_constructs_with_empty_timeline_default():
    tj = TaskTrajectory(task_id="t1", gmt_create=1, gmt_modify=1)
    assert tj.task_id == "t1"
    assert tj.timeline == []
    assert tj.analysis is None
    assert tj.gmt_create == 1 and tj.gmt_modify == 1


def test_task_trajectory_has_no_phases_or_graph_snapshot():
    field_names = {f.name for f in dataclasses.fields(TaskTrajectory)}
    assert "phases" not in field_names
    assert "graph_snapshot" not in field_names
    assert field_names == {"task_id", "timeline", "analysis", "gmt_create", "gmt_modify"}


def test_task_trajectory_timeline_default_is_per_instance():
    tj1 = TaskTrajectory(task_id="t1", gmt_create=1, gmt_modify=1)
    tj2 = TaskTrajectory(task_id="t2", gmt_create=2, gmt_modify=2)
    assert tj1.timeline == [] and tj2.timeline == []
    assert tj1.timeline is not tj2.timeline


def test_task_timeline_carries_events_by_reference():
    ev = _event(action_type=TrajectoryActionType.SUBMIT, action_result="success")
    tj = TaskTrajectory(task_id="t1", gmt_create=1, gmt_modify=1, timeline=[ev])
    assert len(tj.timeline) == 1
    assert tj.timeline[0] is ev


# --- TrajectoryAnalysis (flattened, no event list) --------------------------

def test_trajectory_analysis_constructs_with_none_defaults():
    ta = TrajectoryAnalysis(
        analysis_type="tc_bot",
        analysis_executor="bot-123",
        analysis_input="event-summary + ext_info digest",
        analysis_output="boost=… failure=…",
        gmt_create=5,
    )
    assert ta.analysis_type == "tc_bot"
    assert ta.analysis_executor == "bot-123"
    assert ta.analysis_input == "event-summary + ext_info digest"
    assert ta.analysis_output == "boost=… failure=…"
    assert ta.boost_reason is None
    assert ta.failure_reason is None
    assert ta.gmt_create == 5


def test_trajectory_analysis_has_no_event_list():
    field_names = {f.name for f in dataclasses.fields(TrajectoryAnalysis)}
    assert "events" not in field_names
    assert "timeline" not in field_names
    assert field_names == {
        "analysis_type", "analysis_executor", "analysis_input",
        "analysis_output", "boost_reason", "failure_reason", "gmt_create",
    }


# --- DispatchRationale + nested candidate / join-dropped --------------------

def test_dispatch_rationale_constructs_with_empty_list_defaults():
    r = DispatchRationale(
        strategy_name="search",
        decision_mode="skill",
        join_filter_applied=True,
    )
    assert r.strategy_name == "search"
    assert r.decision_mode == "skill"
    assert r.join_filter_applied is True
    assert r.candidates == []
    assert r.prefetch_tokens == []
    assert r.join_dropped == []
    assert r.skill_prompt_digest is None
    assert r.skill_response_digest is None


def test_dispatch_rationale_list_defaults_are_per_instance():
    r1 = DispatchRationale(strategy_name="s", decision_mode="d", join_filter_applied=False)
    r2 = DispatchRationale(strategy_name="s", decision_mode="d", join_filter_applied=False)
    assert r1.candidates is not r2.candidates
    assert r1.prefetch_tokens is not r2.prefetch_tokens
    assert r1.join_dropped is not r2.join_dropped


def test_dispatch_rationale_carries_nested_candidates_and_dropped():
    cand = DispatchCandidate(
        bot_id="b1",
        recommend_score=0.82,
        short_profile="owns deploy skill",
    )
    # reason is a plain string; claim_filter_disabled is NOT in ReasonCatalog
    # (REQ-7 page), so the field stays str, not ReasonCatalog.
    dropped = JoinDropped(bot_id="b2", reason="claim_filter_disabled")
    r = DispatchRationale(
        strategy_name="search",
        decision_mode="skill",
        join_filter_applied=True,
        candidates=[cand],
        prefetch_tokens=["tk1", "tk2"],
        join_dropped=[dropped],
        skill_prompt_digest="sha256:prompt",
        skill_response_digest="sha256:resp",
    )
    assert r.candidates[0].bot_id == "b1"
    assert r.candidates[0].recommend_score == 0.82
    assert r.candidates[0].short_profile == "owns deploy skill"
    assert r.join_dropped[0].bot_id == "b2"
    assert r.join_dropped[0].reason == "claim_filter_disabled"
    assert r.prefetch_tokens == ["tk1", "tk2"]
    assert r.skill_prompt_digest == "sha256:prompt"
    assert r.skill_response_digest == "sha256:resp"
