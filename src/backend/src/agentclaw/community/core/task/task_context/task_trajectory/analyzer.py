"""``TaskTrajectoryAnalyzer`` — the analysis layer (REQ-9, P5a).

A thin **multi-executor dispatcher** (``rule`` / ``llm`` / ``tc_bot``) that takes
an assembled ``TaskTrajectory`` + an ``ext_info_lookup`` seam and produces a flat
``TrajectoryAnalysis{analysis_type, analysis_executor, analysis_input,
analysis_output, boost_reason?, failure_reason?, gmt_create}`` — NO event list
(the P0 flattening decision: the analysis object never carries the timeline, so
``json.dumps`` has no recursion path; it is safe to backfill verbatim).

First-iteration wiring (决策 #10/11):
* ``tc_bot`` (LIVE) — calls a bot via the ``send_and_wait_async`` seam the engine's
  planner uses (``OpenApiBotPort``). The bot_id is the deployment-configured
  ``TrajectoryAnalysisConfig.analysis_bot_id`` (read by the P5b service from DI
  config, passed here as ``analysis_executor``). Synchronous with a timeout
  (决策 #10); a bot timeout / call failure / unparseable response RAISES
  ``TrajectoryAnalysisError`` — the P5b service maps that to HTTP 504 and does
  NOT backfill (决策 #14's swallow waiver is EMISSION-only, not analysis).
  The bot's input message carries an **ext_info 概要** (REQ-9: analysis_input
  = "事件数 + ext_info 概要") alongside the event counts — the "为何" signals
  the bot needs to derive ``boost_reason`` (last dispatch rationale:
  strategy/mode/candidates/join_dropped), ``failure_reason`` (last RESET
  elapsed/threshold, interface_error events) — built by ``_build_ext_info_brief``
  via the same ``_safe_lookup`` defensive read the rule executor uses (a broken
  lookup degrades the brief to ``{}`` so the bot's input stays well-formed:
  the counts-only shape pre-fix).
* ``rule`` (DORMANT) — the deterministic 7-bullet ``failure_reason`` derivation
  (REQ-9 spec lines ~140-146) + ``boost_reason`` from the last DISPATCH event's
  ``ext_info["_dispatch_rationale"]``. PURE FUNCTION over
  ``(trajectory.timeline, ext_info_lookup)`` — no IO, no external calls,
  deterministic. Unit-tested here; first iteration does NOT auto-trigger it (the
  P5b trigger path uses ``tc_bot``). Available for a future ``analysis_type=rule``
  request param.
* ``llm`` (DEFERRED) — raises ``NotImplementedError`` (决策 #11). The future
  implementation mirrors ``tc_bot`` with an LLM client instead of a bot.

Seams:
* ``ext_info_lookup: Callable[[TrajectoryEvent], dict | None]`` — because the
  domain ``TrajectoryEvent`` does NOT carry ``ext_info`` (REQ-1; the assembler
  dropped it — the analyzer re-queries), the caller (P5b service) supplies a
  callable that maps each event to its parsed ``ext_info`` dict (the full
  envelope the emitter wrote, ``{"schema_v": 1, "_dispatch_rationale": {...},
  "elapsed_ms": ...}``; the analyzer reads the specific keys it needs and
  ignores ``schema_v``). For unit tests, build a fake closure. Keep it
  injectable — ``analyze`` receives it.
* The bot-caller — the same ``OpenApiBotPort`` Protocol the planner
  (``task_plan/strategies.py`` / ``task_dispatch/strategies.py``) uses; injected
  via the analyzer's constructor (DI). For the ``rule`` executor (no bot call),
  ``bot=None`` is fine.

Scope boundary (P5a vs P5b): this module is the analyzer ONLY — no HTTP
endpoint, no service orchestration, no ``backfill_analysis`` (those are P5b).
No FastAPI / transport, no ``NodeAction`` / ``append_action_event`` /
``task_action_log`` (决策 #14 / spec invariant — the analyzer is a
domain/service-layer object).

Authoritative: ``specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md``
REQ-9 (7-bullet ``failure_reason`` derivation, ``boost_reason`` from last
DISPATCH, ``analysis_type ∈ {llm, tc_bot, rule}``, ``analysis_executor``,
``analysis_input``/``output``, ``gmt_create``, JSON-no-recursion) + 决策 #10/11
(rule dormant first iteration, tc_bot live, bot_id DI-config) + 决策 #13 (backfill
overwrite is P5b; the analyzer just returns the object) + 决策 #14 (analysis
failures raise, not swallowed).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Protocol, runtime_checkable

from agentclaw.community.core.task.domain.errors import TrajectoryAnalysisError
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    AnalysisType,
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryAnalysis,
    TrajectoryEvent,
)

logger = logging.getLogger("task.trajectory")


# ---------------------------------------------------------------------------
# Bot-caller seam — the same shape the planner / dispatcher uses
# ---------------------------------------------------------------------------


@runtime_checkable
class _BotCaller(Protocol):
    """Structural slice of ``OpenApiBotPort`` the analyzer needs.

    Mirrors the ``send_and_wait_async`` seam ``task_plan/strategies.py`` and
    ``task_dispatch/strategies.py`` call (``await self._bot.send_and_wait_async(
    bot_id=..., message=..., metadata=...)``). The full ``OpenApiBotPort`` Protocol
    is the canonical contract; this local slice lets unit tests pass a tiny fake
    without importing the full DI-bound Protocol.
    """

    async def send_and_wait_async(
        self,
        *,
        bot_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class _AnalysisConfig(Protocol):
    """Structural slice of the DI ``TrajectoryAnalysisConfig`` the analyzer
    reads. Defined locally (not imported from ``di.config``) to avoid a
    domain→DI layering dependency; the real config structurally satisfies it."""

    @property
    def tc_bot_timeout_seconds(self) -> float:
        ...

    @property
    def analysis_bot_id(self) -> str | None:
        ...


# ---------------------------------------------------------------------------
# Helpers — terminal-status gate + analysis_input/output assembly (shared by
# all executors; the bot executor feeds the same summary the rule executor
# builds, so the two executors present a consistent input record).
# ---------------------------------------------------------------------------


# Terminal-status set (spec.md:147 — "末条 **terminal** TRANSITION 事件"):
# the analysis ``failure_reason`` derivation fires ONLY when the last terminal
# transition lands in a terminal status. "Terminal" means the task has stopped
# for good — the intermediate states (PENDING/PLANNING/RUNNING/DONE) keep the
# task open (DONE is "执行完成,但尚未通过验收" — accepts verdict yet to come),
# so a non-terminal transition (e.g. a still-running task's PENDING→RUNNING or
# RUNNING→DONE flip) must NOT fire the rule derivation (else bullet-7 would
# emit a misleading ``unclassified`` failure_reason on a task that is not
# actually done). Expressed as a ``frozenset`` so a future ``Status`` addition
# is caught at import time (membership is explicit, not inferred).
_TERMINAL_STATUSES: frozenset[Status] = frozenset({
    Status.SUCCESS,    # 执行完成且已通过验收
    Status.FAILED,      # 执行或验收失败
    Status.HUNG,        # 已挂起(需人介入)
    Status.CANCELLED,   # 已取消
})


def _terminal_status(timeline: list[TrajectoryEvent]) -> Status | None:
    """The last **terminal** ``action_type=transition`` event's ``status_to``,
    with a FALLBACK to the last event of any action_type whose ``status_to`` is
    terminal when no terminal TRANSITION row exists. REQ-9 (spec.md:147 +
    fallback note): ``failure_reason`` is derived only when the task is terminal
    non-SUCCESS, determined by this event.

    Primary path (spec REQ-9 "末条 terminal TRANSITION 事件"): walk TRANSITION
    events backward, return the first ``status_to`` in ``_TERMINAL_STATUSES``
    — intermediate transitions (PENDING→RUNNING, …→DONE) are SKIPPED.

    Fallback path (the engine does NOT always log a terminal TRANSITION row —
    e.g. the acceptance-FAIL path routes HUNG via ``_escalate_hung``
    (engine.py ``on_report`` L2069-2188), which BYPASSES the ``_hung_and_escalate``
    transition gate site, so a real acceptance-FAIL task's trajectory has a
    VERIFY event with ``action_result="accept_fail"`` + ``status_to=HUNG`` but
    NO terminal TRANSITION row). When no terminal transition is found, derive
    the terminal status from the last event whose ``status_to`` is terminal —
    the trajectory's events carry ``status_to`` (EXECUTE/VERIFY/RESET record
    the post-action status), so the timeline reflects the graph's terminal
    status even without a dedicated transition row. This lets REQ-9 bullet 6
    (``acceptance_failed:``) fire on the real acceptance-FAIL path (the VERIFY
    event's ``action_result="accept_fail"`` matches bullet 6) instead of
    silently returning ``None``.

    Returns ``None`` ONLY when NO event has a terminal ``status_to`` (the task
    is genuinely still open / not terminal). The 4 green e2e cases
    (success/interface_error/timeout/hung) all have a real terminal TRANSITION
    row, so the fallback NEVER engages for them — they stay on the primary
    path."""
    # 1. Prefer a terminal TRANSITION row (spec REQ-9 "末条 terminal TRANSITION 事件").
    for ev in reversed(timeline):
        if ev.action_type == TrajectoryActionType.TRANSITION and ev.status_to in _TERMINAL_STATUSES:
            return ev.status_to
    # 2. FALLBACK: the engine does not always log a terminal TRANSITION (e.g.
    #    acceptance-FAIL routes HUNG via _escalate_hung, bypassing the
    #    transition gate). Derive the terminal status from the last event whose
    #    status_to is terminal — the trajectory reflects the graph's terminal
    #    status even without a dedicated transition row.
    for ev in reversed(timeline):
        if ev.status_to in _TERMINAL_STATUSES:
            return ev.status_to
    return None


def _build_analysis_input(
    timeline: list[TrajectoryEvent],
    ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
) -> str:
    """A short, deterministic summary of the analysis input: event count +
    per-``action_type`` counts + which events carry a dispatch rationale / RESET
    metrics (REQ-9 ``analysis_input``) + the **ext_info 概要** (REQ-9: the
    analysis_input is the "事件数 + ext_info 概要" record) so the live
    ``tc_bot`` path's input message carries the "为何" signals alongside the
    counts (the rule executor also reads ext_info directly via ``_derive_*``).

    Deterministic order: events are counted in ``action_type`` value order so
    the same timeline always yields the same string (stable across runs / test
    assertions). The ``ext_info``概要 flags which events carry the specific
    ext_info payloads the rule executor reads (``_dispatch_rationale`` for
    DISPATCH, ``elapsed_ms`` for RESET) — the operator-facing summary of "what
    enrichment is available" — followed by the structured brief's string form
    (``_format_ext_info_brief``) so a single read of ``analysis_input`` tells
    the operator AND the bot the "为何" without parsing the timeline.
    """
    counts: dict[str, int] = {}
    dispatch_with_rationale = 0
    reset_with_metrics = 0
    for ev in timeline:
        key = ev.action_type.value if isinstance(ev.action_type, TrajectoryActionType) else str(ev.action_type)
        counts[key] = counts.get(key, 0) + 1
        try:
            ext = ext_info_lookup(ev) or {}
        except Exception:  # noqa: BLE001  lookup must not crash the summary
            ext = {}
        if ev.action_type == TrajectoryActionType.DISPATCH and isinstance(ext, dict) and "_dispatch_rationale" in ext:
            dispatch_with_rationale += 1
        if ev.action_type == TrajectoryActionType.RESET and isinstance(ext, dict) and "elapsed_ms" in ext:
            reset_with_metrics += 1
    parts = [f"events={len(timeline)}"]
    for at in sorted(counts):
        parts.append(f"{at}={counts[at]}")
    parts.append(f"dispatch_with_rationale={dispatch_with_rationale}")
    parts.append(f"reset_with_metrics={reset_with_metrics}")
    # ext_info 概要 (REQ-9): the "为何" signals alongside the counts so the
    # tc_bot input (and the rule executor's analysis_input record) carry the
    # dispatch rationale / RESET SLA / interface-error summary in-line. The
    # brief degrades to "" when the lookup is empty/broken (graceful — the
    # counts-only shape is preserved, the bot is not misled with empty keys).
    brief = _build_ext_info_brief(timeline, ext_info_lookup)
    ext_summary = _format_ext_info_brief(brief)
    if ext_summary:
        parts.append(f"ext_info概要={ext_summary}")
    return "; ".join(parts)


def _build_analysis_output(boost: str | None, failure: str | None) -> str:
    """``analysis_output`` = the ``boost_reason``/``failure_reason`` 拼装 conclusion
    text (REQ-9). Both may be None (e.g. a still-running task with no dispatch);
    the output is then empty rather than a misleading string."""
    pieces: list[str] = []
    if boost is not None:
        pieces.append(f"boost_reason: {boost}")
    if failure is not None:
        pieces.append(f"failure_reason: {failure}")
    return "; ".join(pieces)


# ---------------------------------------------------------------------------
# rule executor — the 7-bullet failure_reason derivation (PURE FUNCTION)
# ---------------------------------------------------------------------------


def _derive_failure_reason(
    timeline: list[TrajectoryEvent],
    ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
) -> str | None:
    """The 7-bullet ``failure_reason`` derivation (REQ-9 spec lines ~140-146).

    Gate: ``failure_reason`` only when the task is terminal NON-SUCCESS — by
    the last ``action_type=transition`` event's ``status_to``; ``SUCCESS`` →
    ``None``; no terminal transition → ``None`` (the task is not terminal, the
    analysis is informational, not a failure verdict).

    Walk the timeline backward per bullet, applying the 7 bullets in PRIORITY
    ORDER; the first bullet that finds a matching event produces the
    ``failure_reason``. The priority reflects "the decisive terminal event": a
    SLA-timeout RESET (bullet 1) is more decisive than an interface error
    (bullet 2) which is more decisive than a dispatch-stuck RESET (bullet 3),
    etc. Bullet 7 is the unclassified fallback (the last event's summary).

    Carry-notes:
    * ``action_type``/``error_type``/``action_result`` are LOWERCASE; ``status_to``
      is UPPERCASE (``Status`` enum ``.value``). Compared accordingly.
    * Bullet 1 ``elapsed_ms``/``sla_threshold_ms`` come from THAT RESET event's
      ``ext_info`` (the P3-3 RESET gate stored them there).
    * Bullet 4 ``hung_reason`` = the event's ``error_msg`` (the domain
      ``TrajectoryEvent`` carries no separate ``hung_reason`` field).
    """
    # Terminal gate.
    terminal = _terminal_status(timeline)
    if terminal is None:
        return None  # not terminal (no transition) → no failure verdict
    if terminal == Status.SUCCESS:
        return None  # terminal SUCCESS → failure_reason=None (spec)

    # Bullet 1: last RESET with action_result="sla_timeout" OR
    #            error_type=EXECUTION_TIMEOUT → execution_timeout with
    #            elapsed/threshold from that RESET event's ext_info.
    for ev in reversed(timeline):
        if ev.action_type == TrajectoryActionType.RESET and (
            ev.action_result == "sla_timeout"
            or ev.error_type == ReasonCatalog.EXECUTION_TIMEOUT
        ):
            ext = _safe_lookup(ext_info_lookup, ev)
            elapsed = ext.get("elapsed_ms") if ext else None
            threshold = ext.get("sla_threshold_ms") if ext else None
            return f"execution_timeout: 在 {elapsed}ms 触发,阈值 {threshold}ms"

    # Bullet 2: most recent EXECUTE/VERIFY with error_type=UNDERLYING_INTERFACE_ERROR.
    for ev in reversed(timeline):
        if ev.action_type in (TrajectoryActionType.EXECUTE, TrajectoryActionType.VERIFY) and (
            ev.error_type == ReasonCatalog.UNDERLYING_INTERFACE_ERROR
        ):
            return f"underlying_interface_error: {ev.error_msg or ''}"

    # Bullet 3: RESET with action_result="pending_dispatch_stuck" →
    #           dispatch_stuck with elapsed from ext_info.
    for ev in reversed(timeline):
        if ev.action_type == TrajectoryActionType.RESET and ev.action_result == "pending_dispatch_stuck":
            ext = _safe_lookup(ext_info_lookup, ev)
            elapsed = ext.get("elapsed_ms") if ext else None
            return f"dispatch_stuck: 停留 {elapsed}ms"

    # Bullet 4: any event with error_type=HUNG → hung with the hung_reason
    #           (the event's error_msg).
    for ev in reversed(timeline):
        if ev.error_type == ReasonCatalog.HUNG:
            return f"hung: {ev.error_msg or ''}"

    # Bullet 5: last PLAN with action_result ∈ {parse_fail, call_fail} →
    #           plan_failure with the origin (the action_result itself).
    for ev in reversed(timeline):
        if ev.action_type == TrajectoryActionType.PLAN and ev.action_result in ("parse_fail", "call_fail"):
            return f"plan_failure: {ev.action_result}"

    # Bullet 6: acceptance FAIL — EXECUTE/VERIFY with action_result="accept_fail"
    #           → acceptance_failed with the acceptance_result (action_result).
    for ev in reversed(timeline):
        if ev.action_type in (TrajectoryActionType.EXECUTE, TrajectoryActionType.VERIFY) and (
            ev.action_result == "accept_fail"
        ):
            return f"acceptance_failed: {ev.action_result}"

    # Bullet 7: unclassified — the most recent event summary
    #           (action_type + action_result + error_msg).
    if timeline:
        last = timeline[-1]
        at = last.action_type.value if isinstance(last.action_type, TrajectoryActionType) else str(last.action_type)
        summary = f"{at} {last.action_result}"
        if last.error_msg:
            summary += f": {last.error_msg}"
        return f"unclassified: {summary}"
    return None


def _derive_boost_reason(
    timeline: list[TrajectoryEvent],
    ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
) -> str | None:
    """``boost_reason`` from the LAST ``action_type=dispatch`` event's
    ``ext_info["_dispatch_rationale"]`` (REQ-9).

    Format: ``"策略={strategy_name} 模式={decision_mode} 选中={assignee}
    ({action_result}); 候选{candidate_count} 取最优; JOIN 丢={join_dropped 摘要}"``.

    * ``assignee`` — the dispatched target (the dispatch event's ``action_input``
      — the node spec / target content — or the rationale's first candidate's
      ``bot_id`` when ``action_input`` is absent).
    * ``candidate_count`` — ``len(rationale.candidates)``.
    * ``join_dropped 摘要`` — ``{count}个({comma-joined reasons, first 2})`` /
      ``0个``.

    Degrades to ``None`` when:
    * there is no DISPATCH event in the timeline (no dispatch to explain), OR
    * the last DISPATCH event's ``ext_info`` carries no ``_dispatch_rationale``
      (a static-plan group dispatch that never wrote one — ``hit_multi`` — OR a
      BBS / exec-retry-replay path that skipped the rationale write). The
      carry-note explicitly forbids crashing on these; a ``None`` boost is the
      honest "no dispatch rationale available" signal.
    """
    last_dispatch: TrajectoryEvent | None = None
    for ev in reversed(timeline):
        if ev.action_type == TrajectoryActionType.DISPATCH:
            last_dispatch = ev
            break
    if last_dispatch is None:
        return None
    ext = _safe_lookup(ext_info_lookup, last_dispatch) or {}
    rationale = ext.get("_dispatch_rationale") if isinstance(ext, dict) else None
    if not isinstance(rationale, dict) or not rationale:
        return None  # static-plan / BBS / replay — no rationale written → None
    strategy_name = rationale.get("strategy_name", "unknown")
    decision_mode = rationale.get("decision_mode", "unknown")
    candidates = rationale.get("candidates") or []
    candidate_count = len(candidates)
    join_dropped = rationale.get("join_dropped") or []
    # assignee: the dispatch event's action_input (the node spec / target),
    # else the rationale's first candidate's bot_id.
    assignee = last_dispatch.action_input
    if not assignee and candidates and isinstance(candidates[0], dict):
        assignee = candidates[0].get("bot_id")
    if not assignee:
        assignee = "unknown"
    action_result = last_dispatch.action_result
    # join_dropped 摘要: count + a couple reasons (readable for ops).
    if join_dropped:
        reasons = ",".join(
            str(d.get("reason", "")) for d in join_dropped[:2] if isinstance(d, dict)
        )
        jd_summary = f"{len(join_dropped)}个({reasons})"
    else:
        jd_summary = "0个"
    return (
        f"策略={strategy_name} 模式={decision_mode} 选中={assignee}({action_result}); "
        f"候选{candidate_count} 取最优; JOIN 丢={jd_summary}"
    )


def _safe_lookup(
    ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
    ev: TrajectoryEvent,
) -> dict | None:
    """Call ``ext_info_lookup`` defensively — a lookup that raises (e.g. a
    broken P5b service closure) must not crash the PURE-FUNCTION rule executor;
    degrade to ``None`` + WARNING (a missing ext_info downgrades a bullet's
    detail rather than breaking the analysis)."""
    try:
        return ext_info_lookup(ev)
    except Exception as ex:  # noqa: BLE001  pure-function must not raise on IO
        logger.warning(
            "[task][trajectory] ext_info_lookup 失败 task=%s node=%s action=%s: %s",
            ev.task_id, ev.node_id, ev.action_type, ex,
        )
        return None


def _build_ext_info_brief(
    timeline: list[TrajectoryEvent],
    ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
) -> dict:
    """Build the **ext_info 概要** (REQ-9: ``analysis_input`` = "事件数 +
    ext_info 概要") the LIVE ``tc_bot`` path feeds to the bot alongside the
    event counts — the "为何" signals the bot needs to derive
    ``boost_reason``/``failure_reason`` (dispatch rationale, RESET SLA,
    interface-error origin). Reuses the SAME ``_safe_lookup`` defensive read
    the rule executor uses (broken lookup → degrade to empties, don't crash;
    the bot's input then degrades to event counts only — the same shape the
    ``tc_bot`` path produced before this fix).

    Included keys (each OMITTED when the underlying ``ext_info`` is absent, so
    the bot's prompt can branch on key presence without parsing around
    ``null`` placeholders):
    * ``last_dispatch_rationale`` — 概要 of the LAST ``action_type=dispatch``
      event's ``ext_info["_dispatch_rationale"]`` (strategy_name,
      decision_mode, candidate_count, top candidate scores, join_dropped
      count + first few reasons). Gives the bot the "派发为何" signal to
      derive ``boost_reason`` (decision #10 / REQ-9 ``boost_reason``).
    * ``last_reset_sla`` — ``elapsed_ms`` / ``sla_threshold_ms`` from the LAST
      ``action_type=reset`` event whose ``action_result ∈ {sla_timeout,
      pending_dispatch_stuck}``. Gives the bot the "执行超时 / 派发卡死"
      signal to derive the ``execution_timeout`` / ``dispatch_stuck`` detail
      (REQ-4 / REQ-9 bullet 1/3).
    * ``interface_error_events`` — indices whose ``ext_info`` surfaces an
      ``interface_error_code`` (the bot_interface error origin, REQ-5).
      Gives the bot the "底层接口报错" signal.

    Returns an EMPTY dict when the lookup returns nothing usable (the
    structured shape is stable, so a missing key = signal absent). The
    associated string form (``_format_ext_info_brief``) then returns ``""``
    and the bot's ``analysis_input`` keeps the pre-fix counts-only shape —
    graceful degradation is the whole point of the ``_safe_lookup`` reuse.
    """
    brief: dict[str, Any] = {}
    # Last DISPATCH event's _dispatch_rationale 概要 → boost_reason signal.
    for ev in reversed(timeline):
        if ev.action_type == TrajectoryActionType.DISPATCH:
            ext = _safe_lookup(ext_info_lookup, ev) or {}
            rationale = ext.get("_dispatch_rationale") if isinstance(ext, dict) else None
            if isinstance(rationale, dict) and rationale:
                candidates = rationale.get("candidates") or []
                top_scores = [
                    c.get("recommend_score")
                    for c in candidates[:3]
                    if isinstance(c, dict)
                ]
                join_dropped = rationale.get("join_dropped") or []
                jd_reasons: list[str] = []
                for d in join_dropped[:3]:
                    if isinstance(d, dict):
                        reason = d.get("reason")
                        if reason is not None:
                            jd_reasons.append(str(reason))
                brief["last_dispatch_rationale"] = {
                    "strategy_name": rationale.get("strategy_name"),
                    "decision_mode": rationale.get("decision_mode"),
                    "candidate_count": len(candidates),
                    "top_candidate_scores": top_scores,
                    "join_dropped_summary": {
                        "count": len(join_dropped),
                        "reasons": jd_reasons,
                    },
                }
            break
    # Last RESET with sla_timeout / pending_dispatch_stuck → elapsed/threshold
    # (the "执行超时 / 派发卡死" detail the bot needs for failure_reason bullet
    # 1/3 — the rule executor reads the same ext_info keys directly). Only
    # added when the lookup returned a real ext_info dict (``None`` ⇒ the event
    # has NO ext_info written at all ⇒ signal absent; degrade to "key not in
    # brief" rather than a placeholder with ``None``-for-everything that would
    # mislead the bot into thinking the RESET had a malformed metric).
    for ev in reversed(timeline):
        if (
            ev.action_type == TrajectoryActionType.RESET
            and ev.action_result in ("sla_timeout", "pending_dispatch_stuck")
        ):
            ext = _safe_lookup(ext_info_lookup, ev)
            if isinstance(ext, dict):
                brief["last_reset_sla"] = {
                    "action_result": ev.action_result,
                    "elapsed_ms": ext.get("elapsed_ms"),
                    "sla_threshold_ms": ext.get("sla_threshold_ms"),
                }
            break
    # Events whose ext_info surfaces an interface_error_code (the bot_interface
    # error origin, REQ-5) — the "底层接口报错" signal.
    iface_events: list[dict[str, Any]] = []
    for idx, ev in enumerate(timeline):
        ext = _safe_lookup(ext_info_lookup, ev)
        if isinstance(ext, dict) and ext.get("interface_error_code") is not None:
            iface_events.append({
                "index": idx,
                "action_type": (
                    ev.action_type.value
                    if isinstance(ev.action_type, TrajectoryActionType)
                    else str(ev.action_type)
                ),
                "interface_error_code": ext.get("interface_error_code"),
            })
    if iface_events:
        brief["interface_error_events"] = iface_events
    return brief


def _format_ext_info_brief(brief: dict[str, Any]) -> str:
    """Format the ``ext_info`` 概要 dict as a compact summary string for the
    ``analysis_input`` field (REQ-9 — the analysis_input carries "事件数 +
    ext_info 概要" as a flat record). Mirrors the structured ``ext_info_brief``
    field the bot message carries; both degrade together (empty dict → ``""``
    so the analysis_input keeps the pre-fix counts-only shape when the lookup
    is empty / broken)."""
    pieces: list[str] = []
    dr = brief.get("last_dispatch_rationale")
    if isinstance(dr, dict):
        jd = dr.get("join_dropped_summary") or {}
        pieces.append(
            f"dispatch[last:策略={dr.get('strategy_name')}/模式={dr.get('decision_mode')}"
            f"/候选{dr.get('candidate_count', 0)}/JOIN丢{jd.get('count', 0)}]"
        )
    rs = brief.get("last_reset_sla")
    if isinstance(rs, dict):
        pieces.append(
            f"reset_sla[last:{rs.get('action_result')} "
            f"elapsed={rs.get('elapsed_ms')}ms threshold={rs.get('sla_threshold_ms')}ms]"
        )
    ie_count = len(brief.get("interface_error_events") or [])
    if ie_count:
        pieces.append(f"interface_err[{ie_count}]")
    return "; ".join(pieces)


# ---------------------------------------------------------------------------
# Analyzer — the multi-executor dispatcher
# ---------------------------------------------------------------------------


class TaskTrajectoryAnalyzer:
    """Multi-executor trajectory analysis dispatcher (``rule`` / ``llm`` /
    ``tc_bot``).

    The analyzer is constructed by DI with a bot-caller (the ``OpenApiBotPort``
    seam) + a ``TrajectoryAnalysisConfig`` (timeout). The ``analyze`` method
    receives the trajectory, the ``ext_info_lookup`` callable, and the
    ``analysis_type`` / ``analysis_executor`` to dispatch on. ``rule`` is a pure
    function; ``tc_bot`` calls the bot synchronously with a timeout; ``llm`` is
    deferred (raises ``NotImplementedError``).

    No transport / FastAPI / ``NodeAction`` / ``append_action_event`` /
    ``task_action_log`` references — the analyzer is a domain/service-layer
    object (决策 #14 / spec invariant).
    """

    def __init__(
        self,
        bot: _BotCaller | None = None,
        config: _AnalysisConfig | None = None,
    ) -> None:
        self._bot = bot
        self._config = config or _DefaultAnalysisConfig()

    async def analyze(
        self,
        trajectory: TaskTrajectory,
        ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
        *,
        analysis_type: AnalysisType,
        analysis_executor: str,
    ) -> TrajectoryAnalysis:
        """Dispatch to the configured executor and return a flat
        ``TrajectoryAnalysis`` (no event list).

        ``analysis_type`` selects the executor (``rule``/``llm``/``tc_bot``).
        ``analysis_executor`` is the executor's own id — ``"rule_engine"`` for
        the rule executor, the configured ``bot_id`` for ``tc_bot`` (read by the
        P5b service from ``TrajectoryAnalysisConfig.analysis_bot_id`` and passed
        here), the model name for ``llm``. Both accept the ``AnalysisType`` enum
        OR the bare ``str`` form (``StrEnum`` equality).
        """
        # Normalize to AnalysisType so both the enum and bare-string forms work.
        at = analysis_type if isinstance(analysis_type, AnalysisType) else AnalysisType(str(analysis_type))
        if at == AnalysisType.RULE:
            return self._analyze_rule(trajectory, ext_info_lookup, analysis_executor)
        if at == AnalysisType.TC_BOT:
            return await self._analyze_tc_bot(trajectory, ext_info_lookup, analysis_executor)
        if at == AnalysisType.LLM:
            raise NotImplementedError(
                "llm analysis executor not wired in first iteration (决策 #11)"
            )
        # Unknown analysis_type — defensive (AnalysisType normalization above
        # rejects unknowns, but a stray subclass could slip through).
        raise ValueError(f"unknown analysis_type: {analysis_type!r}")

    # ------------------------------------------------------------------
    # rule executor — pure function, deterministic, no IO
    # ------------------------------------------------------------------

    def _analyze_rule(
        self,
        trajectory: TaskTrajectory,
        ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
        analysis_executor: str,
    ) -> TrajectoryAnalysis:
        """The deterministic 7-bullet rule executor (REQ-9).

        PURE FUNCTION over ``(trajectory.timeline, ext_info_lookup)``: no IO,
        no external calls, deterministic. First iteration keeps it DORMANT (not
        auto-triggered — 决策 #11); available for a future
        ``analysis_type=rule`` request param. ``failure_reason`` is derived only
        when the task is terminal non-SUCCESS; ``boost_reason`` from the last
        DISPATCH event's ``ext_info["_dispatch_rationale"]``.
        """
        timeline = trajectory.timeline
        failure = _derive_failure_reason(timeline, ext_info_lookup)
        boost = _derive_boost_reason(timeline, ext_info_lookup)
        analysis_input = _build_analysis_input(timeline, ext_info_lookup)
        analysis_output = _build_analysis_output(boost, failure)
        return TrajectoryAnalysis(
            analysis_type=AnalysisType.RULE,
            analysis_executor=analysis_executor,
            analysis_input=analysis_input,
            analysis_output=analysis_output,
            gmt_create=int(time.time() * 1000),
            boost_reason=boost,
            failure_reason=failure,
        )

    # ------------------------------------------------------------------
    # tc_bot executor — calls the configured bot via send_and_wait_async
    # ------------------------------------------------------------------

    async def _analyze_tc_bot(
        self,
        trajectory: TaskTrajectory,
        ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
        analysis_executor: str,
    ) -> TrajectoryAnalysis:
        """Call the configured bot (REQ-9 + 决策 #10) and wrap its response as a
        ``TrajectoryAnalysis``.

        The bot_id is the ``analysis_executor`` param (the P5b service reads it
        from ``TrajectoryAnalysisConfig.analysis_bot_id`` and passes it — NOT a
        per-request param). The bot is fed a structured JSON message (the
        trajectory summary + analysis_input) and asked to return a JSON response
        with ``analysis_output`` (required) + optional ``boost_reason`` /
        ``failure_reason``.

        Synchronous with a timeout (决策 #10): the bot call uses
        ``send_and_wait_async(..., timeout=...)``. On timeout / call failure /
        unparseable response, the executor RAISES ``TrajectoryAnalysisError``
        (the P5b service maps to HTTP 504 and does NOT backfill — 决策 #14's
        swallow waiver is emission-only, not analysis).
        """
        if self._bot is None:
            raise TrajectoryAnalysisError(
                "tc_bot analysis executor requires a bot caller (OpenApiBotPort) "
                "to be wired in DI; got bot=None"
            )
        analysis_input = _build_analysis_input(trajectory.timeline, ext_info_lookup)
        message = self._build_bot_message(trajectory, analysis_input, ext_info_lookup)
        try:
            run = await self._bot.send_and_wait_async(
                bot_id=analysis_executor,
                message=message,
                metadata={"phase": "trajectory_analysis"},
                timeout=self._config.tc_bot_timeout_seconds,
            )
        except TrajectoryAnalysisError:
            raise
        except Exception as ex:  # noqa: BLE001  bot timeout / call failure → domain error
            raise TrajectoryAnalysisError(
                f"tc_bot analysis failed (bot_id={analysis_executor}): {type(ex).__name__}: {ex}"
            ) from ex
        return self._parse_bot_response(run, analysis_executor, analysis_input)

    def _build_bot_message(
        self,
        trajectory: TaskTrajectory,
        analysis_input: str,
        ext_info_lookup: Callable[[TrajectoryEvent], dict | None],
    ) -> str:
        """Build the structured JSON message fed to the bot (trajectory summary +
        ``analysis_input`` + **ext_info 概要**). The bot is asked to return a
        JSON response with ``analysis_output`` (required) + optional
        ``boost_reason`` / ``failure_reason`` (the contract documented in the
        module docstring).

        The ``ext_info_brief`` field (REQ-9 — analysis_input = "事件数 +
        ext_info 概要") carries the structured "为何" signals the bot needs to
        derive ``boost_reason`` (last dispatch rationale: strategy/mode/
        candidates/join_dropped) and ``failure_reason`` (last RESET
        elapsed/threshold, interface_error events) — the live ``tc_bot`` path
        was counts-only before this fix, so the bot could not produce a
        meaningful ``boost_reason`` or the ``execution_timeout`` detail. The
        brief degrades to ``{}`` when the lookup is empty/broken
        (``_safe_lookup`` swallows + WARNING), so the bot's input stays
        well-formed (a missing key = signal absent, NOT a ``null`` placeholder
        the bot would have to parse around)."""
        timeline_brief = [
            {
                "action_type": (
                    ev.action_type.value
                    if isinstance(ev.action_type, TrajectoryActionType) else str(ev.action_type)
                ),
                "action_result": ev.action_result,
                "error_type": ev.error_type.value if isinstance(ev.error_type, ReasonCatalog) else ev.error_type,
                "error_msg": ev.error_msg,
                "status_to": ev.status_to.value if isinstance(ev.status_to, Status) else ev.status_to,
                "attempt": ev.attempt,
            }
            for ev in trajectory.timeline
        ]
        ext_info_brief = _build_ext_info_brief(trajectory.timeline, ext_info_lookup)
        return json.dumps(
            {
                "task_id": trajectory.task_id,
                "analysis_input": analysis_input,
                "ext_info_brief": ext_info_brief,
                "timeline": timeline_brief,
                "instruction": (
                    "Analyse this task trajectory and return a JSON object with "
                    "'analysis_output' (required), and optional 'boost_reason' and "
                    "'failure_reason' (flat strings). The 'ext_info_brief' field "
                    "carries the dispatch-rationale summary, RESET SLA metrics, and "
                    "interface_error events ('为何' signals — use them to populate "
                    "boost_reason/failure_reason). A missing key in ext_info_brief "
                    "means the signal is absent (not a null placeholder)."
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _parse_bot_response(
        run: dict[str, Any],
        analysis_executor: str,
        analysis_input: str,
    ) -> TrajectoryAnalysis:
        """Parse the bot's ``send_and_wait_async`` response into a
        ``TrajectoryAnalysis``.

        Contract: the bot returns a run dict whose ``result`` is either a bare
        string (the JSON response) or a ``{"content": "<json string>"}`` dict
        (the planner's seam shape). The JSON response MUST carry
        ``analysis_output`` (required, a STRING — a non-str value is a
        contract violation, not a coercion candidate; raising surfaces a bot
        contract bug rather than silently stringifying e.g. a list);
        ``boost_reason`` / ``failure_reason`` are optional (the bot may decline
        to populate them). Missing ``analysis_output`` or an unparseable body
        raises ``TrajectoryAnalysisError`` (decision #10: caller gets a 504, no
        backfill).
        """
        content = _extract_response_content(run)
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError) as ex:
            raise TrajectoryAnalysisError(
                f"tc_bot returned an unparseable response (bot_id={analysis_executor}): {ex}"
            ) from ex
        if not isinstance(parsed, dict):
            raise TrajectoryAnalysisError(
                f"tc_bot response is not a JSON object (bot_id={analysis_executor}): {content!r}"
            )
        analysis_output = parsed.get("analysis_output")
        if not analysis_output:
            raise TrajectoryAnalysisError(
                f"tc_bot response missing required 'analysis_output' "
                f"(bot_id={analysis_executor}): {content!r}"
            )
        if not isinstance(analysis_output, str):  # contract: MUST be a string
            raise TrajectoryAnalysisError(
                f"tc_bot response 'analysis_output' must be a string, got "
                f"{type(analysis_output).__name__} (bot_id={analysis_executor}): {content!r}"
            )
        boost_reason = parsed.get("boost_reason")
        if boost_reason is not None and not isinstance(boost_reason, str):
            boost_reason = str(boost_reason)
        failure_reason = parsed.get("failure_reason")
        if failure_reason is not None and not isinstance(failure_reason, str):
            failure_reason = str(failure_reason)
        return TrajectoryAnalysis(
            analysis_type=AnalysisType.TC_BOT,
            analysis_executor=analysis_executor,
            analysis_input=analysis_input,
            analysis_output=analysis_output,
            gmt_create=int(time.time() * 1000),
            boost_reason=boost_reason,
            failure_reason=failure_reason,
        )


def _extract_response_content(run: dict[str, Any]) -> str:
    """Extract the bot's textual response from the ``send_and_wait_async`` run
    dict. Mirrors the planner's extraction (``task_plan/strategies.py``): the
    response is either ``run["result"]["content"]`` (the seam's content shape)
    or a bare ``run["result"]`` string. A missing result degrades to an empty
    string (which then fails JSON parsing → ``TrajectoryAnalysisError``)."""
    result = run.get("result")
    if isinstance(result, dict):
        return str(result.get("content") or "")
    if result is None:
        return ""
    return str(result)


# ---------------------------------------------------------------------------
# Config — the deployment-configured bot_id + tc_bot timeout (DI-injected)
# ---------------------------------------------------------------------------


class _DefaultAnalysisConfig:
    """In-process default config used when DI did not supply one (lightweight
    tests / pure-core DI injectors that don't bind the
    ``TrajectoryAnalysisConfig``). Matches the structural ``_AnalysisConfig``
    Protocol. The DEFAULT ``analysis_bot_id`` is ``None`` (no bot → the P5b
    service will not call ``tc_bot`` unless the deployment configures one);
    ``tc_bot_timeout_seconds`` mirrors ``OpenApiBotPort.send_and_wait_async``'s
    own default so the analyzer is usable out-of-the-box."""

    analysis_bot_id: str | None = None
    tc_bot_timeout_seconds: float = 180.0
