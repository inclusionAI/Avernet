"""TDD tests for ``TaskTrajectoryAnalyzer`` (REQ-9, P5a — the analysis layer).

The analyzer is a thin multi-executor dispatcher (``rule`` / ``llm`` / ``tc_bot``)
that takes a ``TaskTrajectory`` + an ``ext_info_lookup`` seam and produces a flat
``TrajectoryAnalysis{analysis_type, analysis_executor, analysis_input,
analysis_output, boost_reason?, failure_reason?, gmt_create}`` — NO event list
(no JSON-serialization recursion, per the P0 flattening decision).

Scope (P5a): the analyzer module + ``AnalysisType`` enum (M1).
- ``rule`` executor: the 7-bullet ``failure_reason`` derivation (REQ-9 spec
  lines ~140-146) + ``boost_reason`` from the last DISPATCH event's
  ``ext_info["_dispatch_rationale"]``. PURE FUNCTION, unit-tested. First
  iteration keeps it DORMANT (not auto-triggered — decision #11); the tests
  exercise it directly via ``analysis_type=RULE``.
- ``tc_bot`` executor: calls a bot via the ``send_and_wait_async`` seam the
  planner uses (``OpenApiBotPort``), bot_id from the ``analysis_executor``
  param (deployment-configured via DI's ``TrajectoryAnalysisConfig.analysis_bot_id``,
  read by the P5b service and passed here). Synchronous with a timeout
  (decision #10); a bot timeout/failure RAISES ``TrajectoryAnalysisError`` (the
  P5b service maps to HTTP 504) — failures are NOT swallowed (decision #14's
  swallow waiver is EMISSION-only, not analysis).
- ``llm`` executor: deferred (decision #11) — raises ``NotImplementedError``.

Carry-notes applied:
- ``ext_info.source`` (SUBMIT) vocab = ``TaskSourceType``; the rule analyzer
  ignores it for failure derivation (not a failure signal).
- A ``hit_multi`` dispatch MAY be a static-plan group dispatch that never wrote
  ``_dispatch_rationale`` → ``boost_reason`` degrades to ``None`` (no crash).
- ``status_from``/``status_to`` are UPPERCASE (``Status`` enum .value);
  ``action_type``/``error_type``/``action_result`` are LOWERCASE.
- ``ext_info`` lookup is a ``Callable[[TrajectoryEvent], dict | None]`` — the
  P5b service will build it from the repo's records (which DO carry
  ``ext_info``). Tests pass a fake closure keyed by object identity.

Authoritative: ``specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md``
REQ-9 + 决策 #10/11/13/14.
"""
from __future__ import annotations

import ast
import json
import time
from typing import Any

import pytest

from agentclaw.community.core.task.domain.errors import TrajectoryAnalysisError
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    AnalysisType,
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryAnalysis,
    TrajectoryActionType,
    TrajectoryEvent,
)

# The analyzer module under test (RED: this import fails until implemented).
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)


# ---------------------------------------------------------------------------
# Event / trajectory / lookup helpers
# ---------------------------------------------------------------------------

_MS = 1_700_000_000_000  # arbitrary epoch-ms anchor


def _ev(
    action_type: TrajectoryActionType,
    *,
    action_result: str = "success",
    attempt: int = 0,
    ms: int = _MS,
    node_id: str = "N-1",
    action_input: str | None = None,
    status_from: Status | None = None,
    status_to: Status | None = None,
    error_type: ReasonCatalog | None = None,
    error_msg: str | None = None,
) -> TrajectoryEvent:
    """Build a ``TrajectoryEvent`` with sensible defaults."""
    return TrajectoryEvent(
        task_id="T-1",
        node_id=node_id,
        action_type=action_type,
        action_result=action_result,
        attempt=attempt,
        gmt_create=ms,
        gmt_modified=ms,
        action_input=action_input,
        status_from=status_from,
        status_to=status_to,
        error_type=error_type,
        error_msg=error_msg,
    )


def _traj(timeline: list[TrajectoryEvent]) -> TaskTrajectory:
    """Build a ``TaskTrajectory`` from a timeline (gmt_create = last event)."""
    gmt = timeline[-1].gmt_create if timeline else _MS
    return TaskTrajectory(task_id="T-1", gmt_create=gmt, gmt_modified=gmt, timeline=timeline)


def _lookup(events_ext: list[tuple[TrajectoryEvent, dict]]) -> Any:
    """Build an ``ext_info_lookup`` callable keyed by event object identity.

    The analyzer calls ``ext_info_lookup(event) -> dict | None``. The fake maps
    each test event object (by ``id()``) to its ext_info dict, mirroring the
    contract the P5b service will satisfy (a callable over its event set).
    """
    by_identity = {id(ev): ext for ev, ext in events_ext}

    def lookup(ev: TrajectoryEvent) -> dict | None:
        return by_identity.get(id(ev))

    return lookup


def _dispatch_rationale(
    *,
    strategy_name: str = "search",
    decision_mode: str = "skill",
    candidates: list[dict] | None = None,
    join_dropped: list[dict] | None = None,
) -> dict:
    """Build a ``_dispatch_rationale`` JSON dict as the P3-1 DISPATCH gate wrote
    it inside the ``ext_info`` envelope (``{"_dispatch_rationale": {...}}``)."""
    return {
        "strategy_name": strategy_name,
        "decision_mode": decision_mode,
        "join_filter_applied": True,
        "candidates": candidates if candidates is not None else [
            {"bot_id": "bot-1", "recommend_score": 0.9, "short_profile": "owns skill"},
        ],
        "prefetch_tokens": [],
        "join_dropped": join_dropped if join_dropped is not None else [],
        "skill_prompt_digest": None,
        "skill_response_digest": None,
    }


def _terminal_failed(after: int = _MS + 50_000) -> TrajectoryEvent:
    """A terminal TRANSITION to FAILED (non-SUCCESS) so the rule gate opens."""
    return _ev(
        TrajectoryActionType.TRANSITION,
        action_result="failed",
        ms=after,
        status_from=Status.RUNNING,
        status_to=Status.FAILED,
    )


def _terminal_success(after: int = _MS + 50_000) -> TrajectoryEvent:
    """A terminal TRANSITION to SUCCESS so the rule gate closes (no failure)."""
    return _ev(
        TrajectoryActionType.TRANSITION,
        action_result="success",
        ms=after,
        status_from=Status.RUNNING,
        status_to=Status.SUCCESS,
    )


# ---------------------------------------------------------------------------
# 1. AnalysisType enum (M1)
# ---------------------------------------------------------------------------


def test_analysis_type_has_three_members_with_correct_values():
    assert {e.value for e in AnalysisType} == {"llm", "tc_bot", "rule"}
    assert AnalysisType.LLM.value == "llm"
    assert AnalysisType.TC_BOT.value == "tc_bot"
    assert AnalysisType.RULE.value == "rule"


def test_analysis_type_str_enum_equality_with_bare_strings():
    # StrEnum members compare equal to their value → backward-compat with the
    # bare-string form P0 used (analysis_type="tc_bot").
    assert AnalysisType.TC_BOT == "tc_bot"
    assert AnalysisType.RULE == "rule"
    assert AnalysisType.LLM == "llm"


def test_trajectory_analysis_accepts_analysis_type_enum():
    ta = TrajectoryAnalysis(
        analysis_type=AnalysisType.TC_BOT,
        analysis_executor="bot-123",
        analysis_input="summary",
        analysis_output="conclusion",
        gmt_create=5,
    )
    assert ta.analysis_type is AnalysisType.TC_BOT


def test_trajectory_analysis_still_accepts_str_form_backward_compat():
    # P0 passed bare strings; tightening to AnalysisType must not break that.
    ta = TrajectoryAnalysis(
        analysis_type="tc_bot",
        analysis_executor="bot-123",
        analysis_input="summary",
        analysis_output="conclusion",
        gmt_create=5,
    )
    assert ta.analysis_type == "tc_bot"
    assert ta.analysis_type == AnalysisType.TC_BOT


# ---------------------------------------------------------------------------
# 2. rule executor — the 7-bullet failure_reason derivation
# ---------------------------------------------------------------------------

def _analyze_rule(timeline, ext_info_lookup=None):
    """Synchronous helper: drive the (async) analyzer with RULE."""
    import asyncio
    analyzer = TaskTrajectoryAnalyzer()
    return asyncio.run(
        analyzer.analyze(
            _traj(timeline),
            ext_info_lookup or (lambda ev: None),
            analysis_type=AnalysisType.RULE,
            analysis_executor="rule_engine",
        )
    )


def test_rule_failure_reason_execution_timeout_from_reset_sla_timeout():
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="sla_timeout",
        ms=_MS + 10_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), reset, _terminal_failed()]
    lookup = _lookup([(reset, {"trigger": "sla_timeout", "elapsed_ms": 600000, "sla_threshold_ms": 600000, "attempts_seen": 1})])
    ta = _analyze_rule(timeline, lookup)
    assert ta.failure_reason == "execution_timeout: 在 600000ms 触发,阈值 600000ms"
    assert ta.failure_reason.startswith("execution_timeout:")
    assert ta.analysis_type == AnalysisType.RULE


def test_rule_failure_reason_execution_timeout_from_error_type():
    # A RESET whose error_type is EXECUTION_TIMEOUT (even without action_result
    # == "sla_timeout") also triggers the execution_timeout bullet.
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="exec_failed_retry",
        error_type=ReasonCatalog.EXECUTION_TIMEOUT,
        ms=_MS + 10_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), reset, _terminal_failed()]
    lookup = _lookup([(reset, {"trigger": "sla_timeout", "elapsed_ms": 540000, "sla_threshold_ms": 600000, "attempts_seen": 2})])
    ta = _analyze_rule(timeline, lookup)
    assert ta.failure_reason == "execution_timeout: 在 540000ms 触发,阈值 600000ms"


def test_rule_failure_reason_underlying_interface_error():
    exe = _ev(
        TrajectoryActionType.EXECUTE,
        action_result="failed",
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg="connection refused by upstream",
        ms=_MS + 8_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), exe, _terminal_failed()]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason == "underlying_interface_error: connection refused by upstream"
    assert ta.failure_reason.startswith("underlying_interface_error:")


def test_rule_failure_reason_dispatch_stuck():
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="pending_dispatch_stuck",
        ms=_MS + 12_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), reset, _terminal_failed()]
    lookup = _lookup([(reset, {"trigger": "pending_dispatch_stuck", "elapsed_ms": 180000, "sla_threshold_ms": None, "attempts_seen": 0})])
    ta = _analyze_rule(timeline, lookup)
    assert ta.failure_reason == "dispatch_stuck: 停留 180000ms"
    assert ta.failure_reason.startswith("dispatch_stuck:")


def test_rule_failure_reason_hung():
    # Any event carrying error_type=HUNG → bullet 4 (hung_reason = error_msg).
    exe = _ev(
        TrajectoryActionType.EXECUTE,
        action_result="failed",
        error_type=ReasonCatalog.HUNG,
        error_msg="no progress for 300s",
        ms=_MS + 9_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), exe, _terminal_failed()]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason == "hung: no progress for 300s"
    assert ta.failure_reason.startswith("hung:")


def test_rule_failure_reason_plan_failure_parse_fail():
    plan = _ev(
        TrajectoryActionType.PLAN,
        action_result="parse_fail",
        attempt=2,
        ms=_MS + 6_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), plan, _terminal_failed()]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason == "plan_failure: parse_fail"
    assert ta.failure_reason.startswith("plan_failure:")


def test_rule_failure_reason_plan_failure_call_fail():
    plan = _ev(
        TrajectoryActionType.PLAN,
        action_result="call_fail",
        attempt=1,
        ms=_MS + 6_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), plan, _terminal_failed()]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason == "plan_failure: call_fail"


def test_rule_failure_reason_acceptance_failed():
    verify = _ev(
        TrajectoryActionType.VERIFY,
        action_result="accept_fail",
        ms=_MS + 11_000,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), verify, _terminal_failed()]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason == "acceptance_failed: accept_fail"
    assert ta.failure_reason.startswith("acceptance_failed:")


def test_rule_failure_reason_unclassified_fallback():
    # No decisive RESET/EXECUTE/PLAN/accept event — the terminal transition
    # itself is the last event; bullet 7 summarises it.
    transition = _ev(
        TrajectoryActionType.TRANSITION,
        action_result="failed",
        error_msg="aborted by operator",
        ms=_MS + 20_000,
        status_from=Status.RUNNING,
        status_to=Status.FAILED,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), transition]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason.startswith("unclassified:")
    assert "transition" in ta.failure_reason
    assert "aborted by operator" in ta.failure_reason


def test_rule_unclassified_ignores_submit_rows():
    """Bullet-7's backward walk SKIPS submit rows: SUBMIT is a milestone marker
    (task submission / the enter_bbs BBS-production marker fired synchronously
    in _enter_root_bbs), NOT a terminal cause — a timeline ending in one must
    surface the last SUBSTANTIVE event, never a misleading "submit enter_bbs"."""
    transition = _ev(
        TrajectoryActionType.TRANSITION,
        action_result="failed",
        error_msg="aborted by operator",
        ms=_MS + 20_000,
        status_from=Status.RUNNING,
        status_to=Status.FAILED,
    )
    enter_bbs = _ev(
        TrajectoryActionType.SUBMIT,
        action_result="enter_bbs",
        ms=_MS + 30_000,
    )
    timeline = [transition, enter_bbs]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason.startswith("unclassified:")
    # the summary names the terminal TRANSITION, not the trailing submit marker
    assert "transition" in ta.failure_reason
    assert "aborted by operator" in ta.failure_reason
    assert "enter_bbs" not in ta.failure_reason
    # all-submit timeline → bullet 7 finds nothing substantive → None (not a
    # fabricated "submit"-only summary)
    ta_all_submits = _analyze_rule([enter_bbs])
    assert ta_all_submits.failure_reason is None or "enter_bbs" not in (ta_all_submits.failure_reason or "")


# ---------------------------------------------------------------------------
# 3. rule executor — success task → failure_reason=None
# ---------------------------------------------------------------------------


def test_rule_success_task_failure_reason_none():
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 5_000),
        _terminal_success(),
    ]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason is None


def test_rule_no_terminal_event_failure_reason_none():
    # Case (a): NO event in the timeline has a terminal status_to (no
    # transition AND no EXECUTE/VERIFY/RESET with a terminal status_to). The
    # _terminal_status fallback returns None → failure_reason=None (the task is
    # genuinely still open). This is the ONLY path to None after the fallback.
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 5_000),
    ]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason is None


def test_terminal_status_fallback_from_non_transition_event():
    # Case (b): the engine does NOT always log a terminal TRANSITION row (e.g.
    # acceptance-FAIL routes HUNG via _escalate_hung, bypassing the transition
    # gate). The _terminal_status FALLBACK derives the terminal status from the
    # last event of any action_type whose status_to is terminal. A VERIFY with
    # status_to=HUNG and NO transition → fallback returns HUNG (not None).
    from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
        _terminal_status,
    )
    verify = _ev(
        TrajectoryActionType.VERIFY,
        action_result="accept_fail",
        ms=_MS + 9_000,
        status_from=Status.RUNNING,
        status_to=Status.HUNG,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), verify]
    assert _terminal_status(timeline) is Status.HUNG


def test_acceptance_fail_without_transition_row_derives_acceptance_failed():
    # REAL acceptance-FAIL path: the engine routes acceptance-FAIL to HUNG via
    # _escalate_hung (engine.py on_report L2069-2188), which BYPASSES the
    # _hung_and_escalate transition gate → the trajectory has a VERIFY event
    # with action_result="accept_fail" + status_to=HUNG but NO terminal
    # TRANSITION row. Without the _terminal_status fallback, failure_reason
    # would be None (bullet 6 never fires). WITH the fallback, _terminal_status
    # returns HUNG (from the VERIFY event's status_to), _derive_failure_reason
    # proceeds, and bullet 6 (accept_fail) fires → "acceptance_failed: accept_fail".
    #
    # The fixture mirrors the real path: NO error_type=HUNG event (the HUNG
    # comes from _escalate_hung's status flip, not an error classification), so
    # bullet 4 does NOT preempt bullet 6 here.
    verify = _ev(
        TrajectoryActionType.VERIFY,
        action_result="accept_fail",
        ms=_MS + 9_000,
        status_from=Status.RUNNING,
        status_to=Status.HUNG,
        # error_type intentionally None — the real path's HUNG is a status flip,
        # not an error_type=HUNG classification (so bullet 4 does not preempt
        # bullet 6).
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), verify]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason is not None
    assert ta.failure_reason.startswith("acceptance_failed:")
    assert ta.failure_reason == "acceptance_failed: accept_fail"


def test_rule_non_terminal_transition_failure_reason_none():
    # Intermediate transitions (PENDING→RUNNING, …→DONE) are NOT terminal — DONE
    # is "执行完成,但尚未通过验收" (acceptance verdict still pending). A trajectory
    # whose last transition lands in an intermediate status must yield
    # ``failure_reason=None`` (the I1 terminal-set fix). Without the filter,
    # bullet-7 would emit a misleading ``unclassified`` on this non-terminal task.
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success",
            status_from=None, status_to=Status.PENDING, ms=_MS),
        _ev(
            TrajectoryActionType.TRANSITION, action_result="running",
            ms=_MS + 3_000, status_from=Status.PENDING, status_to=Status.RUNNING,
        ),
        _ev(
            TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 9_000,
        ),
        # A non-terminal "execution done, acceptance pending" → DONE.
        _ev(
            TrajectoryActionType.TRANSITION, action_result="done",
            ms=_MS + 15_000, status_from=Status.RUNNING, status_to=Status.DONE,
        ),
    ]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason is None


def test_rule_terminal_transition_to_hung_fires_hung_bullet():
    # HUNG is terminal (the task stopped — 需人介入). The terminal gate accepts
    # HUNG as terminal non-SUCCESS, and bullet 4 fires (the transition event
    # itself carries error_type=HUNG + the hung_reason as its error_msg).
    transition = _ev(
        TrajectoryActionType.TRANSITION,
        action_result="hung",
        error_type=ReasonCatalog.HUNG,
        error_msg="stuck at step 3 for 300s",
        ms=_MS + 20_000,
        status_from=Status.RUNNING,
        status_to=Status.HUNG,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), transition]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason == "hung: stuck at step 3 for 300s"
    assert ta.failure_reason.startswith("hung:")


def test_rule_terminal_transition_to_cancelled_yields_unclassified():
    # CANCELLED is terminal (the task stopped — 已取消) but no decisive
    # RESET/EXECUTE/PLAN/accept failure event precedes it → bullet 7 fires the
    # unclassified fallback. Locking in the behavior so a future Status addition
    # / terminal-set change can't silently regress (the gate must accept
    # CANCELLED, and with no more-specific bullet, bullet 7 summarises the last
    # event — the transition itself).
    transition = _ev(
        TrajectoryActionType.TRANSITION,
        action_result="cancelled",
        error_msg="cancelled by operator",
        ms=_MS + 18_000,
        status_from=Status.RUNNING,
        status_to=Status.CANCELLED,
    )
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), transition]
    ta = _analyze_rule(timeline)
    assert ta.failure_reason.startswith("unclassified:")
    assert "cancel" in ta.failure_reason  # the terminal CANCELLED gate accepted it; bullet 7 summarised


# ---------------------------------------------------------------------------
# 4. rule executor — boost_reason from last dispatch event
# ---------------------------------------------------------------------------


def test_rule_boost_reason_from_last_dispatch_with_rationale():
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 4_000,
    )
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        _ev(TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 5_000),
        _terminal_success(),
    ]
    rationale = _dispatch_rationale(
        strategy_name="search",
        decision_mode="skill",
        candidates=[
            {"bot_id": "bot-1", "recommend_score": 0.9, "short_profile": "owns skill"},
            {"bot_id": "bot-2", "recommend_score": 0.7, "short_profile": "backup"},
            {"bot_id": "bot-3", "recommend_score": 0.5, "short_profile": "rookie"},
        ],
        join_dropped=[{"bot_id": "bot-9", "reason": "claim_mode_off"}],
    )
    lookup = _lookup([(dispatch, {"_dispatch_rationale": rationale})])
    ta = _analyze_rule(timeline, lookup)
    assert ta.boost_reason is not None
    assert "策略=search" in ta.boost_reason
    assert "模式=skill" in ta.boost_reason
    assert "选中=bot-1(hit_single)" in ta.boost_reason
    assert "候选3" in ta.boost_reason
    assert "JOIN 丢=1个(claim_mode_off)" in ta.boost_reason


def test_rule_boost_reason_uses_last_dispatch_when_multiple():
    first = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="miss",
        action_input="bot-old",
        ms=_MS + 2_000,
    )
    last = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-winner",
        ms=_MS + 4_000,
    )
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        first,
        last,
        _terminal_success(),
    ]
    last_rationale = _dispatch_rationale(strategy_name="direct", decision_mode="direct")
    lookup = _lookup([
        (first, {"_dispatch_rationale": _dispatch_rationale(strategy_name="search", decision_mode="skill")}),
        (last, {"_dispatch_rationale": last_rationale}),
    ])
    ta = _analyze_rule(timeline, lookup)
    assert "策略=direct" in ta.boost_reason
    assert "选中=bot-winner(hit_single)" in ta.boost_reason


def test_rule_boost_reason_none_when_no_dispatch():
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 5_000),
        _terminal_success(),
    ]
    ta = _analyze_rule(timeline)
    assert ta.boost_reason is None


def test_rule_boost_reason_fallback_when_hit_multi_no_rationale():
    # A static-plan group dispatch (hit_multi) that never wrote a rationale —
    # must degrade gracefully (None), NOT crash.
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_multi",
        action_input="group-spec",
        ms=_MS + 4_000,
    )
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        _terminal_success(),
    ]
    lookup = _lookup([(dispatch, {"schema_v": 1})])  # no _dispatch_rationale
    ta = _analyze_rule(timeline, lookup)
    assert ta.boost_reason is None


def test_rule_boost_reason_fallback_when_dispatch_has_no_ext_info():
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 4_000,
    )
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        _terminal_success(),
    ]
    # lookup returns None for the dispatch (ext_info absent / BBS replay).
    ta = _analyze_rule(timeline, _lookup([]))
    assert ta.boost_reason is None


# ---------------------------------------------------------------------------
# 5. rule executor — analysis_input / analysis_output / gmt_create
# ---------------------------------------------------------------------------


def test_rule_analysis_input_summarises_event_counts_and_ext_info_signals():
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 3_000,
    )
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="sla_timeout",
        ms=_MS + 9_000,
    )
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.PLAN, action_result="success", ms=_MS + 2_000),
        dispatch,
        _ev(TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 5_000),
        reset,
        _terminal_failed(),
    ]
    rationale = _dispatch_rationale()
    lookup = _lookup([
        (dispatch, {"_dispatch_rationale": rationale}),
        (reset, {"trigger": "sla_timeout", "elapsed_ms": 600000, "sla_threshold_ms": 600000, "attempts_seen": 1}),
    ])
    ta = _analyze_rule(timeline, lookup)
    assert ta.analysis_input is not None
    # event count present
    assert "events=6" in ta.analysis_input
    # per-action-type counts
    assert "submit=1" in ta.analysis_input
    assert "dispatch=1" in ta.analysis_input
    assert "reset=1" in ta.analysis_input
    # which events carry rationales / metrics
    assert "dispatch_with_rationale=1" in ta.analysis_input
    assert "reset_with_metrics=1" in ta.analysis_input
    # I-1: the rule executor's analysis_input is ALSO enriched with the
    # ext_info概要 (the same enrichment the LIVE tc_bot path now feeds the bot).
    # The dispatch rationale 概要 + RESET SLA elapsed/threshold are in the
    # record — the "为何" signals alongside the counts (REQ-9: analysis_input
    # = "事件数 + ext_info 概要", uniform across the rule/tc_bot executors).
    assert "ext_info概要=" in ta.analysis_input
    assert "dispatch[last:策略=search/模式=skill/候选1/JOIN丢0]" in ta.analysis_input
    assert "reset_sla[last:sla_timeout elapsed=600000ms threshold=600000ms]" in ta.analysis_input


def test_rule_analysis_output_joins_boost_and_failure():
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 3_000,
    )
    exe = _ev(
        TrajectoryActionType.EXECUTE,
        action_result="failed",
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg="boom",
        ms=_MS + 5_000,
    )
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        exe,
        _terminal_failed(),
    ]
    rationale = _dispatch_rationale(strategy_name="search", decision_mode="skill")
    lookup = _lookup([(dispatch, {"_dispatch_rationale": rationale})])
    ta = _analyze_rule(timeline, lookup)
    assert ta.analysis_output is not None
    # analysis_output is the boost/failure 拼装 — both present.
    assert "boost_reason" in ta.analysis_output
    assert "策略=search" in ta.analysis_output
    assert "failure_reason" in ta.analysis_output
    assert "underlying_interface_error" in ta.analysis_output


def test_rule_gmt_create_is_analysis_time():
    timeline = [_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()]
    before = int(time.time() * 1000)
    ta = _analyze_rule(timeline)
    after = int(time.time() * 1000)
    assert before <= ta.gmt_create <= after


def test_rule_analysis_executor_is_rule_engine():
    ta = _analyze_rule([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    assert ta.analysis_executor == "rule_engine"


def test_rule_analysis_json_serialisable_no_event_list():
    # The TrajectoryAnalysis must json.dumps cleanly (no recursion into events).
    dispatch = _ev(TrajectoryActionType.DISPATCH, action_result="hit_single", action_input="bot-1", ms=_MS + 3_000)
    timeline = [
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        _ev(TrajectoryActionType.EXECUTE, action_result="failed", error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR, error_msg="boom", ms=_MS + 5_000),
        _terminal_failed(),
    ]
    lookup = _lookup([(dispatch, {"_dispatch_rationale": _dispatch_rationale()})])
    ta = _analyze_rule(timeline, lookup)
    blob = json.dumps(ta, default=vars, ensure_ascii=False)
    parsed = json.loads(blob)
    assert "timeline" not in parsed  # no event list (the P0 flattening decision)
    assert "events" not in parsed
    assert parsed["analysis_type"] == "rule"
    assert parsed["analysis_executor"] == "rule_engine"
    assert "failure_reason" in parsed
    assert "boost_reason" in parsed


# ---------------------------------------------------------------------------
# 6. tc_bot executor
# ---------------------------------------------------------------------------


class _FakeBot:
    """Fake ``OpenApiBotPort``-like seam. Records the last call; scripts the
    response or raises to simulate timeout/failure."""

    def __init__(self, *, response: dict | None = None, content: str | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._response = response
        self._content = content
        self._raise = raise_exc
        self.last_call: dict | None = None

    async def send_and_wait_async(self, *, bot_id, message, metadata=None,
                                  timeout=180.0, poll_interval=2.0) -> dict:
        self.last_call = {
            "bot_id": bot_id, "message": message, "metadata": metadata, "timeout": timeout,
        }
        if self._raise is not None:
            raise self._raise
        if self._content is not None:
            return {"result": {"content": self._content}}
        return self._response or {}


def _bot_analysis_content(*, analysis_output="bot conclusion", boost_reason=None, failure_reason=None) -> str:
    """The JSON contract the tc_bot bot returns as its response content."""
    payload: dict[str, Any] = {"analysis_output": analysis_output}
    if boost_reason is not None:
        payload["boost_reason"] = boost_reason
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_tc_bot_returns_analysis_from_scripted_bot_response():
    bot = _FakeBot(content=_bot_analysis_content(
        analysis_output="root cause: interface down",
        boost_reason="策略=direct 选中=bot-x",
        failure_reason="underlying_interface_error: timeout",
    ))
    analyzer = TaskTrajectoryAnalyzer(bot=bot, config=None)
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.EXECUTE, action_result="failed", error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR, error_msg="timeout", ms=_MS + 5_000),
        _terminal_failed(),
    ])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert ta.analysis_type == AnalysisType.TC_BOT
    assert ta.analysis_executor == "bot-analyst"
    assert ta.analysis_output == "root cause: interface down"
    assert ta.boost_reason == "策略=direct 选中=bot-x"
    assert ta.failure_reason == "underlying_interface_error: timeout"
    assert ta.analysis_input  # the trajectory summary was fed to the bot


@pytest.mark.asyncio
async def test_tc_bot_calls_bot_with_trajectory_summary_and_bot_id():
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.EXECUTE, action_result="success", ms=_MS + 5_000),
        _terminal_success(),
    ])
    await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert bot.last_call is not None
    assert bot.last_call["bot_id"] == "bot-analyst"
    # the message carries the trajectory summary (analysis_input) the rule
    # executor would also build.
    assert "events=" in bot.last_call["message"]
    assert "submit" in bot.last_call["message"]
    assert bot.last_call["metadata"] is not None
    # the default-config timeout (180s) flows through to the bot call (M3).
    assert bot.last_call["timeout"] == 180.0


@pytest.mark.asyncio
async def test_tc_bot_message_includes_running_sessions_section():
    """Mod-2: ``running_sessions`` briefs (nodes still RUNNING + their session
    message excerpts) are folded into the tc_bot message as a dedicated
    section — the LLM's cue to look for session-side problems (tool-call
    errors / 执行完不上报结果) the timeline cannot show."""
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.EXECUTE, action_result="failed", ms=_MS + 5_000),
        _terminal_failed(),
    ])
    briefs = [{
        "node_id": "n1",
        "session_id": "sess-1",
        "elapsed_ms": 3_600_000,
        "message_count": 2,
        "truncated": False,
        "messages": [
            {"role": "user", "content": "run the job"},
            {"role": "assistant", "content": "tool failed: boom"},
        ],
    }]
    await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
        running_sessions=briefs,
    )
    assert bot.last_call is not None
    parsed = json.loads(bot.last_call["message"])
    # the section is present verbatim + the instruction points the LLM at it
    assert parsed["running_sessions"] == briefs
    assert "running_sessions" in parsed["instruction"]


@pytest.mark.asyncio
async def test_tc_bot_message_omits_running_sessions_when_absent():
    """Mod-2: ``running_sessions=None`` (probe disabled / no RUNNING node with a
    readable session) → the key is OMITTED entirely (house convention: a
    missing key = signal absent, NOT a null placeholder) — the pre-probe message
    shape is preserved for existing consumers/tests."""
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _terminal_failed(),
    ])
    await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
        running_sessions=None,
    )
    parsed = json.loads(bot.last_call["message"])
    assert "running_sessions" not in parsed


@pytest.mark.asyncio
async def test_tc_bot_input_includes_ext_info_brief_when_lookup_supplies_it():
    """REQ-9 + I-1: the LIVE tc_bot path must feed the bot the **ext_info 概要**
    (dispatch rationale 概要 + RESET SLA elapsed/threshold + interface-error
    events) so the bot can derive ``boost_reason`` / ``failure_reason`` — not
    the COUNTS-ONLY summary pre-fix. Mirrors a real trajectory: a DISPATCH with
    a rationale (search/skill + 3 candidates + 1 JOIN drop) and a RESET
    ``sla_timeout`` (elapsed/threshold from ext_info)."""
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 4_000,
    )
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="sla_timeout",
        ms=_MS + 9_000,
    )
    exe = _ev(
        TrajectoryActionType.EXECUTE,
        action_result="failed",
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg="boom",
        ms=_MS + 7_000,
    )
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        _ev(TrajectoryActionType.PLAN, action_result="success", ms=_MS + 2_000),
        dispatch,
        exe,
        reset,
        _terminal_failed(),
    ])
    rationale = _dispatch_rationale(
        strategy_name="search",
        decision_mode="skill",
        candidates=[
            {"bot_id": "bot-1", "recommend_score": 0.9, "short_profile": "owns skill"},
            {"bot_id": "bot-2", "recommend_score": 0.7, "short_profile": "backup"},
            {"bot_id": "bot-3", "recommend_score": 0.5, "short_profile": "rookie"},
        ],
        join_dropped=[{"bot_id": "bot-9", "reason": "claim_mode_off"}],
    )
    lookup = _lookup([
        (dispatch, {"_dispatch_rationale": rationale}),
        (exe, {"interface_error_code": "E503"}),
        (reset, {
            "trigger": "sla_timeout",
            "elapsed_ms": 600000,
            "sla_threshold_ms": 600000,
            "attempts_seen": 1,
        }),
    ])
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    ta = await analyzer.analyze(
        trajectory, lookup,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    # The analysis_input STRING carries the ext_info 概要 alongside the counts
    # (REQ-9: analysis_input = "事件数 + ext_info 概要").
    assert ta.analysis_input is not None
    assert "ext_info概要=" in ta.analysis_input
    assert "dispatch[last:策略=search/模式=skill/候选3/JOIN丢1]" in ta.analysis_input
    assert "reset_sla[last:sla_timeout elapsed=600000ms threshold=600000ms]" in ta.analysis_input
    assert "interface_err[1]" in ta.analysis_input
    # The bot's message JSON carries the STRUCTURED ext_info_brief so the bot
    # can read the "为何" signals programmatically (not just from the string).
    assert bot.last_call is not None
    msg = json.loads(bot.last_call["message"])
    assert "ext_info_brief" in msg
    brief = msg["ext_info_brief"]
    assert isinstance(brief, dict)
    # last_dispatch_rationale 概要 (boost_reason signal)
    assert "last_dispatch_rationale" in brief
    dr = brief["last_dispatch_rationale"]
    assert dr["strategy_name"] == "search"
    assert dr["decision_mode"] == "skill"
    assert dr["candidate_count"] == 3
    assert dr["top_candidate_scores"] == [0.9, 0.7, 0.5]
    assert dr["join_dropped_summary"] == {"count": 1, "reasons": ["claim_mode_off"]}
    # last_reset_sla (execution_timeout detail signal)
    assert "last_reset_sla" in brief
    rs = brief["last_reset_sla"]
    assert rs["action_result"] == "sla_timeout"
    assert rs["elapsed_ms"] == 600000
    assert rs["sla_threshold_ms"] == 600000
    # interface_error_events (底层接口报错 signal)
    assert "interface_error_events" in brief
    ie = brief["interface_error_events"]
    assert len(ie) == 1
    assert ie[0]["interface_error_code"] == "E503"
    # The instruction field tells the bot about ext_info_brief's contract.
    assert "ext_info_brief" in msg["instruction"]


@pytest.mark.asyncio
async def test_tc_bot_input_degrades_to_counts_only_when_lookup_empty():
    """I-1 graceful degradation: an EMPTY ext_info_lookup (no ext_info written
    for any event — e.g. a broken P5b service closure) MUST NOT crash the
    tc_bot path or feed the bot a malformed ``ext_info_brief``. The bot input
    degrades to the pre-fix COUNTS-ONLY shape (``ext_info_brief={}``,
    analysis_input has the counts but no ``ext_info概要=`` segment). The "no
    KeyError on a missing key" contract for the bot's prompt is the load-bearing
    assertion: a ``{}`` brief is a well-formed "signal absent" sentinel, NOT a
    ``null`` placeholder the bot would have to parse around."""
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 4_000,
    )
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="sla_timeout",
        ms=_MS + 9_000,
    )
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        reset,
        _terminal_failed(),
    ])
    # Empty lookup → every ext_info_lookup returns None (no ext_info written).
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    ta = await analyzer.analyze(
        trajectory, _lookup([]),
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    # analysis_input keeps the counts-only shape (no ext_info概要 segment).
    assert ta.analysis_input is not None
    assert "events=4" in ta.analysis_input  # SUBMIT + DISPATCH + RESET + TRANSITION(terminal)
    assert "dispatch_with_rationale=0" in ta.analysis_input
    assert "reset_with_metrics=0" in ta.analysis_input
    assert "ext_info概要=" not in ta.analysis_input  # degraded (no brief content)
    # The bot's ext_info_brief is a well-formed empty dict (signal-absent sentinel).
    msg = json.loads(bot.last_call["message"])
    assert msg["ext_info_brief"] == {}
    # No KeyError for any of the bot-prompt's expected brief keys.
    assert "last_dispatch_rationale" not in msg["ext_info_brief"]
    assert "last_reset_sla" not in msg["ext_info_brief"]
    assert "interface_error_events" not in msg["ext_info_brief"]


@pytest.mark.asyncio
async def test_tc_bot_input_degrades_when_lookup_raises_on_dispatch_event():
    """I-1 graceful degradation against a BROKEN lookup (raises on the dispatch
    event): ``_safe_lookup`` swallows + WARNING, the dispatch rationale 概要 is
    skipped, but the bot still gets the rest of the brief (RESET SLA was
    read OK) and the analysis_input carries the counts. The bot is not misled
    with a partial dispatch-rationale entry (no half-curried key)."""
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        action_input="bot-1",
        ms=_MS + 4_000,
    )
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="sla_timeout",
        ms=_MS + 9_000,
    )
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        reset,
        _terminal_failed(),
    ])
    rationale = _dispatch_rationale(strategy_name="search", decision_mode="skill")
    reset_ext = {"trigger": "sla_timeout", "elapsed_ms": 540000, "sla_threshold_ms": 600000}
    by_identity = {id(dispatch): {"_dispatch_rationale": rationale}, id(reset): reset_ext}

    def broken_lookup(ev: TrajectoryEvent) -> dict | None:
        if ev is dispatch:
            raise RuntimeError("lookup boom on dispatch")
        return by_identity.get(id(ev))

    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    ta = await analyzer.analyze(
        trajectory, broken_lookup,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    # The bot still gets a callable result (no crash) + the RESET SLA brief
    # survived; the dispatch rationale 概要 is absent (broken lookup swallowed).
    msg = json.loads(bot.last_call["message"])
    brief = msg["ext_info_brief"]
    assert "last_reset_sla" in brief  # the RESET read was OK
    assert brief["last_reset_sla"]["elapsed_ms"] == 540000
    assert "last_dispatch_rationale" not in brief  # broken lookup → skipped
    # analysis_input carries the counts but NO dispatch[last:...] segment
    # (the dispatch _dispatch_rationale read failed → _safe_lookup returned None
    # → the rationale 概要 is absent, NOT a half-populated entry).
    assert ta.analysis_input is not None
    assert "dispatch=1" in ta.analysis_input
    assert "dispatch[last:" not in ta.analysis_input


@pytest.mark.asyncio
async def test_tc_bot_input_brief_covers_pending_dispatch_stuck_reset():
    """I-1: the RESET SLA brief also covers ``action_result=
    pending_dispatch_stuck`` (REQ-9 bullet 3 — the 'dispatch_stuck: 停留 N ms'
    signal the bot needs), not just ``sla_timeout``. Locking the bullet-3
    coverage so a future change to the RESET ResultCatalog can't silently drop
    it from the bot's brief."""
    dispatch = _ev(
        TrajectoryActionType.DISPATCH,
        action_result="miss",
        action_input=None,
        ms=_MS + 4_000,
    )
    reset = _ev(
        TrajectoryActionType.RESET,
        action_result="pending_dispatch_stuck",
        ms=_MS + 12_000,
    )
    trajectory = _traj([
        _ev(TrajectoryActionType.SUBMIT, action_result="success"),
        dispatch,
        reset,
        _terminal_failed(),
    ])
    reset_ext = {
        "trigger": "pending_dispatch_stuck",
        "elapsed_ms": 180000,
        "sla_threshold_ms": None,
        "attempts_seen": 0,
    }
    lookup = _lookup([(reset, reset_ext)])
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    await analyzer.analyze(
        trajectory, lookup,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    msg = json.loads(bot.last_call["message"])
    brief = msg["ext_info_brief"]
    assert "last_reset_sla" in brief
    assert brief["last_reset_sla"]["action_result"] == "pending_dispatch_stuck"
    assert brief["last_reset_sla"]["elapsed_ms"] == 180000
    assert brief["last_reset_sla"]["sla_threshold_ms"] is None



@pytest.mark.asyncio
async def test_tc_bot_timeout_flows_from_custom_config():
    # A custom TrajectoryAnalysisConfig.tc_bot_timeout_seconds flows end-to-end
    # to the bot call's timeout param (M3 — verifies the config value is used,
    # not just the default).
    from agentclaw.community.di.task_trajectory_config import (
        TrajectoryAnalysisConfig,
    )
    bot = _FakeBot(content=_bot_analysis_content())
    analyzer = TaskTrajectoryAnalyzer(
        bot=bot, config=TrajectoryAnalysisConfig(tc_bot_timeout_seconds=42.0),
    )
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert bot.last_call["timeout"] == 42.0


@pytest.mark.asyncio
async def test_tc_bot_raises_domain_error_on_bot_timeout():
    bot = _FakeBot(raise_exc=TimeoutError("bot did not respond"))
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    with pytest.raises(TrajectoryAnalysisError):
        await analyzer.analyze(
            trajectory, lambda ev: None,
            analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
        )


@pytest.mark.asyncio
async def test_tc_bot_raises_domain_error_on_bot_call_failure():
    bot = _FakeBot(raise_exc=RuntimeError("connection reset"))
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    with pytest.raises(TrajectoryAnalysisError):
        await analyzer.analyze(
            trajectory, lambda ev: None,
            analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
        )


@pytest.mark.asyncio
async def test_tc_bot_raises_domain_error_on_empty_unparseable_response():
    """空/空白响应无可降级内容 → 仍 raise(→504,决策 #10)。非空脏响应见下方
    修复/降级两档(prod 2026-09-22 504 修复后的解析阶梯)。"""
    for content in ("", "   \n  "):
        bot = _FakeBot(content=content)
        analyzer = TaskTrajectoryAnalyzer(bot=bot)
        trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
        with pytest.raises(TrajectoryAnalysisError):
            await analyzer.analyze(
                trajectory, lambda ev: None,
                analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
            )


@pytest.mark.asyncio
async def test_tc_bot_degrades_unparseable_response_to_raw_content():
    """方案B降级:响应非空但不是 JSON(修复 A 也救不回)→ analysis_output =
    响应原文,不 504、不丢 bot 产出;boost/failure 缺省 None。"""
    raw = "this is not json {"
    bot = _FakeBot(content=raw)
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert ta.analysis_output == raw  # 原文保真,不 504
    assert ta.boost_reason is None
    assert ta.failure_reason is None


@pytest.mark.asyncio
async def test_tc_bot_repairs_unescaped_inner_quotes_in_json():
    """方案A修复(线上 504 复现):中文结论里引用工具名的裸双引号
    ("...工具 "query_data" 报错...")使字符串提前闭合 → "Expecting ',' delimiter"。
    修复器给值内裸引号补转义后重解析成功,字段完整取回。"""
    broken = (
        "{\n"
        '"analysis_output": "执行发现工具 "query_data" 报错且一直未上报",\n'
        '"failure_reason": "工具调用报错"\n'
        "}"
    )
    # 首选证明:该文本裸 json.loads 确实复现线上语法错误(非"no JSON")。
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)

    bot = _FakeBot(content=broken)
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert ta.analysis_output == '执行发现工具 "query_data" 报错且一直未上报'
    assert ta.failure_reason == "工具调用报错"


@pytest.mark.asyncio
async def test_tc_bot_repairs_raw_newline_inside_string():
    """方案A修复:字符串值内的裸换行(json.loads 严格模式拒绝的控制字符)
    → 转义为 \\n 后重解析成功。"""
    broken = '{"analysis_output": "第一行\n第二行", "failure_reason": null}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)

    bot = _FakeBot(content=broken)
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert ta.analysis_output == "第一行\n第二行"
    assert ta.failure_reason is None


@pytest.mark.asyncio
async def test_tc_bot_parses_markdown_fenced_json_response():
    # Bot 常把 JSON 包在 ```json 代码块(同 plan/search skill 回投)。_parse_bot_response
    # 经 extract_json 鲁棒抽出,忽略 ``` 包裹 —— 否则裸 json.loads 报
    # "Expecting value: line 1 column 1 (char 0)" → 误判 504。
    fenced = (
        "```json\n"
        + _bot_analysis_content(analysis_output="fenced ok", boost_reason="b")
        + "\n```"
    )
    bot = _FakeBot(content=fenced)
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert ta.analysis_output == "fenced ok"
    assert ta.boost_reason == "b"


@pytest.mark.asyncio
async def test_tc_bot_coerces_structured_analysis_output_to_string():
    # Bot 把分析结论返回成结构化 dict(而非扁平字符串)—— 不再 504;coerce 成 JSON 字符串
    # 保留 bot 的完整结构化分析。修订原 strict-string("must be a string" → raise)行为。
    structured = {
        "analysis_output": {
            "final_status": "SUCCESS",
            "event_breakdown": {"plan": 3, "dispatch": 3},
            "anomalies": [{"type": "plan_parse_fail", "recovered": True}],
        },
    }
    bot = _FakeBot(content=json.dumps(structured, ensure_ascii=False))
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    # analysis_output coerced to a JSON STRING preserving the structured analysis
    assert isinstance(ta.analysis_output, str)
    assert "final_status" in ta.analysis_output and "SUCCESS" in ta.analysis_output
    assert "plan_parse_fail" in ta.analysis_output
    assert ta.failure_reason is None  # success; bot omitted failure_reason


@pytest.mark.asyncio
async def test_tc_bot_raises_domain_error_when_analysis_output_missing():
    bot = _FakeBot(content=json.dumps({"boost_reason": "only boost, no output"}))
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    with pytest.raises(TrajectoryAnalysisError):
        await analyzer.analyze(
            trajectory, lambda ev: None,
            analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
        )


@pytest.mark.asyncio
async def test_tc_bot_accepts_bare_string_result():
    # Some bot seams return ``{"result": "<json string>"}`` (result is a bare
    # string, not a {"content": ...} dict). The parser must handle both shapes.
    bot = _FakeBot(response={"result": _bot_analysis_content(
        analysis_output="bare-string conclusion", failure_reason="hung: stuck",
    )})
    analyzer = TaskTrajectoryAnalyzer(bot=bot)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_failed()])
    ta = await analyzer.analyze(
        trajectory, lambda ev: None,
        analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
    )
    assert ta.failure_reason == "hung: stuck"
    assert ta.analysis_output == "bare-string conclusion"


@pytest.mark.asyncio
async def test_tc_bot_raises_when_no_bot_wired():
    # bot=None + analysis_type=tc_bot → the executor can't call a bot → raise.
    analyzer = TaskTrajectoryAnalyzer(bot=None)
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    with pytest.raises(TrajectoryAnalysisError):
        await analyzer.analyze(
            trajectory, lambda ev: None,
            analysis_type=AnalysisType.TC_BOT, analysis_executor="bot-analyst",
        )


# ---------------------------------------------------------------------------
# 7. llm executor — deferred (decision #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_executor_raises_not_implemented():
    analyzer = TaskTrajectoryAnalyzer()
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success"), _terminal_success()])
    with pytest.raises(NotImplementedError):
        await analyzer.analyze(
            trajectory, lambda ev: None,
            analysis_type=AnalysisType.LLM, analysis_executor="some-llm",
        )


# ---------------------------------------------------------------------------
# 8. dispatch + boundary invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_rejects_unknown_analysis_type():
    analyzer = TaskTrajectoryAnalyzer()
    trajectory = _traj([_ev(TrajectoryActionType.SUBMIT, action_result="success")])
    with pytest.raises((ValueError, KeyError)):
        await analyzer.analyze(
            trajectory, lambda ev: None,
            analysis_type="bogus", analysis_executor="x",
        )


def test_analyzer_does_not_import_transport_or_node_action():
    """决策 #14 / spec invariant: the analyzer is a domain/service-layer object;
    no FastAPI / transport, no ``NodeAction`` / ``append_action_event`` /
    ``task_action_log`` reference. The check walks the AST (imports + name
    references) — NOT raw string matching, which would false-positive on the
    module docstring that documents what the analyzer must NOT touch."""
    import agentclaw.community.core.task.task_context.task_trajectory.analyzer as mod
    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    forbidden_name_refs = {"NodeAction", "append_action_event", "task_action_log"}
    forbidden_import_modules = {
        "fastapi", "uvicorn", "starlette",
        "agentclaw.community.adapters.http",
    }
    # No forbidden name references in code (Name / Attribute nodes only —
    # docstrings and string literals are not Name nodes, so the module's own
    # docstring mentioning "NodeAction" as a prohibition does not trip this).
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden_name_refs:
            pytest.fail(f"analyzer references forbidden name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in forbidden_name_refs:
            pytest.fail(f"analyzer references forbidden attribute: {node.attr}")
    # No forbidden imports (module-level or inside functions).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _assert_import_allowed(alias.name, forbidden_import_modules)
        elif isinstance(node, ast.ImportFrom):
            _assert_import_allowed(node.module or "", forbidden_import_modules)


def _assert_import_allowed(module: str, forbidden: set[str]) -> None:
    for fm in forbidden:
        if module == fm or module.startswith(fm + "."):
            pytest.fail(f"analyzer must not import {module} (transport/FastAPI layer)")


# ---------------------------------------------------------------------------
# 9. DI registration — the analyzer resolves from TaskPersistenceModule
# ---------------------------------------------------------------------------


def test_analyzer_is_bound_in_di_module():
    """The analyzer must be registered in the task persistence DI module so
    the P5b service can ``Injected(...)`` it. Resolving it from an injector
    configured with ``TestingDatabaseModule`` + ``TaskPersistenceModule`` must
    yield a working ``TaskTrajectoryAnalyzer``; the optional ``OpenApiBotPort``
    and ``TrajectoryAnalysisConfig`` resolve to ``None`` / the default (neither
    is bound in the test injector, mirroring how the assembler's DI test
    exercises only the binding, not the full end-to-end)."""
    from injector import Injector
    from agentclaw.community.di.modules.testing_database_module import (
        TestingDatabaseModule,
    )
    from agentclaw.community.di.modules.task_persistence_module import (
        TaskPersistenceModule,
    )

    injector = Injector([TestingDatabaseModule(), TaskPersistenceModule()])
    analyzer = injector.get(TaskTrajectoryAnalyzer)
    assert isinstance(analyzer, TaskTrajectoryAnalyzer)
    # The bot port + config are NOT bound in this test injector → the provider
    # falls back to None / the default config (rule executor still works; tc_bot
    # would raise TrajectoryAnalysisError when invoked).
    assert analyzer._bot is None
    assert analyzer._config is not None
    assert analyzer._config.tc_bot_timeout_seconds == 180.0
    # Singleton: resolving twice returns the same instance.
    assert injector.get(TaskTrajectoryAnalyzer) is analyzer
