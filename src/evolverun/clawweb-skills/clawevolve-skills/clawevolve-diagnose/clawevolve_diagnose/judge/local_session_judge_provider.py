from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import logger
from ..constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
from ..models import (
    CasePreference,
    Diagnosis,
    JudgeRuntimeConfig,
    LlmRuntimeConfig,
    SessionRow,
    normalize_judge_backend,
)
from ..selection import parse_session_datetime, select_diagnoses, session_row_datetime
from ..utils import redact_secrets
from .analyzer_factory import create_session_analyzer
from .keyless_session_prompt import OUTPUT_SCHEMA_VERSION
from .ocsa_contract import (
    OCSA_ANALYSIS_STYLE,
    OCSA_JUDGE_STRATEGY,
    OCSA_PROTOCOL_VERSION,
)


DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS = 8
DEFAULT_MAX_JUDGE_ROUNDS = 100
DEFAULT_JUDGE_SESSION_BATCH_SIZE = DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS


def _judge_analysis_style(runtime: JudgeRuntimeConfig) -> str:
    return (
        OCSA_ANALYSIS_STYLE
        if normalize_judge_backend(runtime.backend) == "api"
        else "diagnose_native_single_session_llm"
    )


def _judge_strategy(runtime: JudgeRuntimeConfig) -> str:
    return (
        OCSA_JUDGE_STRATEGY
        if normalize_judge_backend(runtime.backend) == "api"
        else "local_session_diagnose_native_llm_chronological_batches"
    )


def _judge_protocol_version(backend: str) -> str:
    return (
        OCSA_PROTOCOL_VERSION
        if normalize_judge_backend(backend) == "api"
        else OUTPUT_SCHEMA_VERSION
    )


@dataclass
class LocalSessionJudgeResult:
    diagnoses: list[Diagnosis]
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class LocalSessionJudgeProvider:
    """Local-session analyzer backed by the selected judge implementation.

    The production API backend sends complete normalized Session evidence to
    OCSA session_report. Diagnose then performs request matching, eval eligibility,
    faithful query construction, ranking, and quota selection outside OCSA.
    """

    def __init__(self, runtime: JudgeRuntimeConfig, artifact_dir: Path | None = None):
        self.runtime = runtime
        self.artifact_dir = artifact_dir
        self._active_analyzer: Any | None = None
        self.judge_order = "chronological"
        if normalize_judge_backend(self.runtime.backend) == "subagent":
            # OpenClaw subagent calls are process/session based. Keep the first
            # keyless implementation strictly sequential for stability.
            self.max_concurrent_tasks = 1
        else:
            self.max_concurrent_tasks = max(
                1,
                DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS,
            )
        self.max_judge_rounds = max(
            1,
            DEFAULT_MAX_JUDGE_ROUNDS,
        )
        self.session_batch_size = max(1, DEFAULT_JUDGE_SESSION_BATCH_SIZE)
        self.judge_limit = self.session_batch_size

    def analyze_until_selectable(
        self, rows: list[SessionRow], bot_id: str, preference: CasePreference
    ) -> LocalSessionJudgeResult:
        try:
            return self._analyze_until_selectable(rows, bot_id, preference)
        finally:
            self._close_active_analyzer()

    def _analyze_until_selectable(
        self, rows: list[SessionRow], bot_id: str, preference: CasePreference
    ) -> LocalSessionJudgeResult:
        candidate_rows = _sort_rows_newest(_scope_rows_to_local_candidates(rows, bot_id))
        scoped_rows = _filter_rows_by_preference_time(candidate_rows, preference)
        backend = normalize_judge_backend(self.runtime.backend)
        analysis_style = _judge_analysis_style(self.runtime)
        judge_strategy = _judge_strategy(self.runtime)
        _progress(
            "local judge scope prepared",
            input_rows=len(rows),
            local_candidate_rows=len(candidate_rows),
            time_scoped_rows=len(scoped_rows),
            time_range_label=preference.time_range_label,
            since=preference.since,
            until=preference.until,
            mode=judge_strategy,
            judge_analysis_style=analysis_style,
            judge_order=self.judge_order,
            session_batch_size=self.session_batch_size,
            per_batch_judge_limit=self.judge_limit,
            max_judge_rounds=self.max_judge_rounds,
            judge_backend=backend,
            api_key_configured=bool(self.runtime.api.api_key),
            subagent_configured=backend == "subagent",
        )
        if not scoped_rows:
            return LocalSessionJudgeResult(
                diagnoses=[],
                stats={
                    "strategy": judge_strategy,
                    "judge_analysis_style": analysis_style,
                    "input_local_session_count": len(rows),
                    "time_scoped_local_session_count": 0,
                    "local_candidate_session_count": len(candidate_rows),
                    "time_range_label": preference.time_range_label,
                    "candidate_acquisition": {
                        "input_rows": len(rows),
                        "local_candidate_rows": len(candidate_rows),
                        "time_scoped_rows": 0,
                    },
                    "configured_judge_limit": self.judge_limit,
                    "judge_order": self.judge_order,
                    "session_batch_size": self.session_batch_size,
                    "max_concurrent_tasks": self.max_concurrent_tasks,
                    "max_judge_rounds": self.max_judge_rounds,
                    "no_sessions": not rows,
                    "judged_session_count": 0,
                    "analyzed_session_count": 0,
                    "final_diagnosis_count": 0,
                    "judge_backend": normalize_judge_backend(self.runtime.backend),
                    "judge_requires_api_key": backend == "api",
                    "direct_api_configured": bool(self.runtime.api.api_key),
                    "api_key_configured": bool(self.runtime.api.api_key),
                    "subagent_configured": backend == "subagent",
                },
                warnings=[
                    _empty_local_candidate_warning(
                        rows,
                        candidate_rows,
                        {"outside_time_window": len(candidate_rows) if candidate_rows else 0},
                    )
                ],
            )
        return self._analyze_with_selected_judge(scoped_rows, rows, bot_id, preference)

    def _analyze_with_selected_judge(
        self,
        scoped_rows: list[SessionRow],
        all_rows: list[SessionRow],
        bot_id: str,
        preference: CasePreference,
    ) -> LocalSessionJudgeResult:
        backend = normalize_judge_backend(self.runtime.backend)
        analysis_style = _judge_analysis_style(self.runtime)
        judge_strategy = _judge_strategy(self.runtime)
        try:
            _progress(
                "selected judge analyzer init start",
                judge_backend=backend,
                judge_analysis_style=analysis_style,
                judge_protocol_version=_judge_protocol_version(backend),
                model=self.runtime.model,
                max_concurrent_tasks=self.max_concurrent_tasks,
                timeout_seconds=DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
                subagent_agent_id=(
                    self.runtime.subagent.agent_id if backend == "subagent" else ""
                ),
                subagent_workspace=(
                    self.runtime.subagent.workspace if backend == "subagent" else ""
                ),
                direct_api_base_url=self.runtime.base_url if backend == "api" else "",
            )
            analyzer = create_session_analyzer(
                self.runtime,
                preference,
                max_concurrent_tasks=self.max_concurrent_tasks,
                artifact_dir=self.artifact_dir,
            )
            self._active_analyzer = analyzer
            _progress(
                "selected judge analyzer init done",
                judge_backend=backend,
                judge_analysis_style=analysis_style,
                max_concurrent_tasks=self.max_concurrent_tasks,
            )
        except Exception as exc:  # noqa: BLE001 - keep bot-facing skill structured on judge setup failure.
            safe_error = _safe_runtime_error(exc, self.runtime)
            return LocalSessionJudgeResult(
                diagnoses=[],
                stats={
                    "strategy": judge_strategy,
                    "input_local_session_count": len(all_rows),
                    "time_scoped_local_session_count": len(scoped_rows),
                    "chronological_order": "created_at_or_mtime_desc",
                    "judge_order": self.judge_order,
                    "candidate_acquisition": {
                        "mode": "chronological_batched_judge",
                        "input_rows": len(all_rows),
                        "local_candidate_rows": len(scoped_rows),
                    },
                    "judge_limit": 0,
                    "configured_judge_limit": self.judge_limit,
                    "session_batch_size": self.session_batch_size,
                    "max_concurrent_tasks": self.max_concurrent_tasks,
                    "max_judge_rounds": self.max_judge_rounds,
                    "judged_session_count": 0,
                    "failed_session_count": 0,
                    "final_diagnosis_count": 0,
                    "selected_count_at_judge_stop": 0,
                    "judge_unavailable": True,
                    "ocsa_session_report_judge_unavailable": backend == "api",
                    "judge_requires_api_key": backend == "api",
                    "error": safe_error,
                    "judge_protocol_version": _judge_protocol_version(backend),
                    "judge_backend": backend,
                    "judge_analysis_style": analysis_style,
                    "judge_model": self.runtime.model,
                    "direct_api_base_url": self.runtime.base_url,
                    "subagent_agent_id": (
                        self.runtime.subagent.agent_id if backend == "subagent" else ""
                    ),
                },
                warnings=[
                    f"Local judge ({analysis_style}) could not be initialized; "
                    f"no fallback classifier was used. error={safe_error}"
                ],
            )

        diagnoses: list[Diagnosis] = []
        session_identity = _SessionIdentityTracker()
        judged = 0
        failed_count = 0
        failure_samples: list[str] = []
        rounds_started = 0
        stop_reason = "candidate_pool_exhausted"
        final_status = _selection_status(diagnoses, preference)
        batch_stats: list[dict[str, Any]] = []
        total_judge_candidates = 0
        total_prejudge_duplicates = 0
        total_empty_batches = 0
        worker_count = max(1, self.max_concurrent_tasks)
        analysis_session_limit = (
            preference.max_sessions
            if preference.max_sessions and preference.max_sessions > 0
            else None
        )

        _progress(
            f"local chronological judge start sessions={len(scoped_rows)} "
            f"judge_order={self.judge_order} raw_batch_size={self.session_batch_size} "
            f"per_batch_judge_limit={self.judge_limit} "
            f"concurrency={self.max_concurrent_tasks} max_rounds={self.max_judge_rounds}",
            requested_cases=preference.case_limit,
            requested_bad=preference.bad_case_count,
            requested_good=preference.good_case_count,
            judge_analysis_style=analysis_style,
            analysis_session_limit=analysis_session_limit or "unbounded",
        )

        with ThreadPoolExecutor(max_workers=self.max_concurrent_tasks) as executor:
            batch_start = 0
            while batch_start < len(scoped_rows):
                if analysis_session_limit is not None and judged >= analysis_session_limit:
                    stop_reason = "analysis_session_limit_reached"
                    break
                if rounds_started >= self.max_judge_rounds:
                    stop_reason = "max_judge_rounds_reached"
                    break
                rounds_started += 1
                round_judge_limit = _remaining_judge_limit(
                    final_status, self.judge_limit
                )
                raw_batch = scoped_rows[
                    batch_start : batch_start + round_judge_limit
                ]
                batch_start_after = batch_start + len(raw_batch)
                _progress(
                    "session batch selected",
                    round=rounds_started,
                    batch_start=batch_start + 1,
                    batch_end=batch_start + len(raw_batch),
                    raw_batch_size=len(raw_batch),
                    sessions=_rows_debug(raw_batch),
                )
                deduped_batch: list[SessionRow] = []
                duplicate_reasons: Counter[str] = Counter()
                for row in raw_batch:
                    reason = session_identity.duplicate_reason(row)
                    if reason:
                        duplicate_reasons[reason] += 1
                        continue
                    # Do not mark the row here.  A session that is not sent to
                    # the selected judge in this round must not block an older
                    # useful session for the same task. Mark only rows that are
                    # actually selected for judge analysis.
                    deduped_batch.append(row)

                judge_candidate_rows = deduped_batch
                judge_candidate_count = len(deduped_batch)
                _progress(
                    "judge candidate acquisition done",
                    round=rounds_started,
                    deduped=len(deduped_batch),
                    judge_candidate_count=judge_candidate_count,
                    candidate_sessions=_rows_debug(judge_candidate_rows),
                )

                remaining_analysis_budget = (
                    analysis_session_limit - judged
                    if analysis_session_limit is not None
                    else None
                )
                accepted_rows: list[SessionRow] = []
                for row in judge_candidate_rows:
                    if remaining_analysis_budget is not None and len(accepted_rows) >= remaining_analysis_budget:
                        break
                    reason = session_identity.duplicate_reason(row)
                    if reason:
                        duplicate_reasons[reason] += 1
                        continue
                    session_identity.reserve(row)
                    accepted_rows.append(row)
                    if len(accepted_rows) >= round_judge_limit:
                        break

                total_judge_candidates += len(accepted_rows)
                total_prejudge_duplicates += sum(duplicate_reasons.values())
                batch_record: dict[str, Any] = {
                    "round": rounds_started,
                    "raw_batch_start": batch_start + 1,
                    "raw_batch_end": batch_start + len(raw_batch),
                    "raw_batch_size": len(raw_batch),
                    "prejudge_duplicate_skipped": sum(duplicate_reasons.values()),
                    "prejudge_duplicate_by_reason": dict(duplicate_reasons),
                    "after_prejudge_dedupe": len(deduped_batch),
                    "judge_candidate_count": judge_candidate_count,
                    "prejudge_duplicate_skipped_total": sum(duplicate_reasons.values()),
                    "judge_selected_for_analysis": len(accepted_rows),
                    "round_judge_limit": round_judge_limit,
                    "remaining_analysis_budget_before_round": remaining_analysis_budget,
                    "raw_sessions": _rows_debug(raw_batch),
                    "judge_selected_sessions": _rows_debug(accepted_rows),
                }
                _progress(
                    f"local judge round {rounds_started}/{self.max_judge_rounds} "
                    f"sessions={batch_start + 1}-{batch_start + len(raw_batch)}/{len(scoped_rows)} "
                    f"raw={len(raw_batch)} deduped={len(deduped_batch)} selected={len(accepted_rows)}",
                    duplicate_skipped=sum(duplicate_reasons.values()),
                )
                if not accepted_rows:
                    total_empty_batches += 1
                    batch_record["judge_diagnosis_count"] = 0
                    batch_record["selected_after_round"] = final_status.get("selected_count", 0)
                    batch_stats.append(batch_record)
                    batch_start = batch_start_after
                    continue

                chunks = _equal_judge_chunks(
                    accepted_rows, min(worker_count, len(accepted_rows))
                )
                _progress(
                    "session judge chunks scheduled",
                    round=rounds_started,
                    chunks=len(chunks),
                    chunk_sizes=[len(c) for c in chunks],
                    sessions=_rows_debug(accepted_rows),
                )
                future_to_chunk = {
                    executor.submit(analyzer.analyze, chunk, bot_id): (idx, chunk)
                    for idx, chunk in enumerate(chunks, start=1)
                }
                round_diagnoses: list[Diagnosis] = []
                round_failed = 0
                for future in as_completed(future_to_chunk):
                    chunk_index, chunk = future_to_chunk[future]
                    try:
                        chunk_diagnoses = future.result()
                    except Exception as exc:  # noqa: BLE001 - analyzer normally catches per-session errors.
                        _progress(
                            f"local judge failed chunk {chunk_index}/{len(chunks)} "
                            f"size={len(chunk)} error={_safe_runtime_error(exc, self.runtime)}"
                        )
                        round_failed += len(chunk)
                        chunk_diagnoses = []
                    round_diagnoses.extend(chunk_diagnoses)
                    judged += len(chunk)
                    _progress(
                        f"local judge done chunk {chunk_index}/{len(chunks)} "
                        f"judged={judged} diagnoses={len(chunk_diagnoses)}",
                        chunk_sessions=_rows_debug(chunk),
                        diagnosis_modes=[d.evolution_failure_mode for d in chunk_diagnoses[:10]],
                        diagnosis_types=[d.case_type for d in chunk_diagnoses[:10]],
                    )
                accepted_round_diagnoses: list[Diagnosis] = []
                diagnosed_session_ids = {
                    str(diag.session.session_id or "").strip()
                    for diag in round_diagnoses
                    if str(diag.session.session_id or "").strip()
                }
                for row in accepted_rows:
                    sid = str(row.session_id or "").strip()
                    if sid and sid not in diagnosed_session_ids:
                        session_identity.release(row)
                for diag in round_diagnoses:
                    if diag.symptom_class in {
                        "SESSION_JUDGE_ERROR",
                        "OCSA_SESSION_REPORT_JUDGE_ERROR",
                        "LLM_AS_JUDGE_ERROR",
                    }:
                        # Judge/infrastructure failures are analysis telemetry,
                        # never candidate eval cases. Release only the input
                        # session reservation; no cross-session semantic filter
                        # is applied after OCSA analysis.
                        round_failed += 1
                        failure_detail = str(diag.root_cause_summary or "").strip()
                        if failure_detail and failure_detail not in failure_samples:
                            failure_samples.append(failure_detail[:4000])
                            del failure_samples[3:]
                        session_identity.release(diag)
                        continue
                    accepted_round_diagnoses.append(diag)
                diagnoses.extend(accepted_round_diagnoses)
                failed_count += round_failed
                final_status = _selection_status(diagnoses, preference)
                batch_record.update(
                    {
                        "judge_raw_diagnosis_count": len(round_diagnoses),
                        "judge_accepted_diagnosis_count": len(accepted_round_diagnoses),
                        "judge_failed_session_count": round_failed,
                        "post_ocsa_dedupe_enabled": False,
                        "selected_after_round": final_status.get("selected_count", 0),
                        "selected_good_after_round": final_status.get("selected_good", 0),
                        "selected_bad_after_round": final_status.get("selected_bad", 0),
                        "selection_satisfied": final_status.get("satisfied", False),
                    }
                )
                _progress(
                    "judge round summarized",
                    round=rounds_started,
                    judge_raw_diagnoses=len(round_diagnoses),
                    judge_accepted_diagnoses=len(accepted_round_diagnoses),
                    judge_failed_sessions=round_failed,
                    post_ocsa_dedupe_enabled=False,
                    selected_after_round=final_status.get("selected_count", 0),
                    selected_good_after_round=final_status.get("selected_good", 0),
                    selected_bad_after_round=final_status.get("selected_bad", 0),
                    selection_satisfied=final_status.get("satisfied", False),
                )
                batch_stats.append(batch_record)
                if final_status["satisfied"]:
                    stop_reason = "selection_satisfied"
                    break
                if backend == "api" and _diagnoses_include_nonretryable_auth_failure(round_diagnoses):
                    # Never fabricate diagnoses when the semantic judge is unavailable.
                    # A rule-based fallback cannot reliably infer user intent, failure
                    # class, or a standalone eval query from a trajectory.
                    stop_reason = "judge_auth_failed"
                    _progress(
                        "local judge stopped after non-retryable API authentication failure",
                        round=rounds_started,
                        judged=judged,
                        failed=failed_count,
                    )
                    break
                if analysis_session_limit is not None and judged >= analysis_session_limit:
                    stop_reason = "analysis_session_limit_reached"
                    break
                batch_start = batch_start_after
                _progress(
                    "local judge continue: "
                    f"selected={final_status['selected_count']}/{preference.case_limit} "
                    f"good={final_status['selected_good']}/{final_status['required_good']} "
                    f"bad={final_status['selected_bad']}/{final_status['required_bad']}"
                )

        if (
            stop_reason == "candidate_pool_exhausted"
            and rounds_started >= self.max_judge_rounds
            and not final_status.get("satisfied", False)
            and len(scoped_rows) > rounds_started * self.session_batch_size
        ):
            stop_reason = "max_judge_rounds_reached"
        selected_now = int(final_status.get("selected_count", _selected_count(diagnoses, preference)))
        warnings: list[str] = []
        if any(_diagnoses_include_nonretryable_auth_failure([diag]) for diag in diagnoses):
            warnings.append(
                "API judge failed with a non-retryable authentication error (for example HTTP 401/API key not found). "
                "The LLM endpoint was reachable and an Authorization header was sent, so this is not the local 600s timeout; "
                "check the execution environment that launched diagnose, because the process may be receiving a different or truncated OPENAI_API_KEY than your interactive shell."
            )
        elif judged > 0 and failed_count >= judged:
            warnings.append(
                "All local judge assessments failed; no rule fallback was used. "
                "For API backend, check API key, base URL, model name, and network/DNS "
                "connectivity. For subagent backend, check openclaw agent creation, "
                "workspace/model configuration, and session file readability."
            )
        if not final_status.get("satisfied", False) and not stop_reason.startswith("judge_auth_failed"):
            warnings.append(
                f"local judge selected {selected_now}/{preference.case_limit} "
                f"with {analysis_style} after judging {judged} sessions "
                f"across {rounds_started} chronological batches; "
                f"good={final_status.get('selected_good', 0)}/{final_status.get('required_good', 0)} "
                f"bad={final_status.get('selected_bad', 0)}/{final_status.get('required_bad', 0)}. "
                "This is a normal partial completion when the configured time/session window "
                "does not contain enough qualified cases. Increase --max-sessions "
                "or use a wider time range if more coverage is needed."
            )
        _progress(
            "local chronological judge stop",
            stop_reason=stop_reason,
            judged=judged,
            rounds_started=rounds_started,
            diagnoses=len(diagnoses),
            failed=failed_count,
            selected=selected_now,
            selected_good=final_status.get("selected_good", 0),
            required_good=final_status.get("required_good", 0),
            selected_bad=final_status.get("selected_bad", 0),
            required_bad=final_status.get("required_bad", 0),
            satisfied=final_status.get("satisfied", False),
            duplicate_session_inputs=total_prejudge_duplicates,
            post_ocsa_dedupe_enabled=False,
        )
        return LocalSessionJudgeResult(
            sorted(diagnoses, key=lambda d: (d.quality_score, d.confidence), reverse=True),
            stats={
                "strategy": judge_strategy,
                "input_local_session_count": len(all_rows),
                "time_scoped_local_session_count": len(scoped_rows),
                "chronological_order": "created_at_or_mtime_desc",
                "judge_order": self.judge_order,
                "candidate_acquisition": {
                    "mode": "chronological_batched_judge",
                    "input_rows": len(all_rows),
                    "local_candidate_rows": len(scoped_rows),
                    "judge_candidates": total_judge_candidates,
                    "empty_batches": total_empty_batches,
                    "requested_modes": preference.normalized_modes(),
                    "focus_terms": preference.focus_terms,
                    "required_terms": getattr(preference, "required_terms", []),
                    "excluded_terms": getattr(preference, "excluded_terms", []),
                    "time_range_label": preference.time_range_label,
                },
                "dedupe": session_identity.stats(total_prejudge_duplicates),
                "judge_limit": total_judge_candidates,
                "configured_judge_limit": self.judge_limit,
                "analysis_session_limit": analysis_session_limit,
                "max_sessions_semantics": "actual_judged_sessions_not_discovered_rows",
                "session_batch_size": self.session_batch_size,
                "max_concurrent_tasks": self.max_concurrent_tasks,
                "judge_thread_count": worker_count,
                "judge_rounds_started": rounds_started,
                "max_judge_rounds": self.max_judge_rounds,
                "judge_stop_reason": stop_reason,
                "selection_status_at_judge_stop": final_status,
                "judged_session_count": judged,
                "failed_session_count": failed_count,
                "all_judge_assessments_failed": judged > 0 and failed_count >= judged,
                "judge_failure_samples": failure_samples,
                "final_diagnosis_count": len(diagnoses),
                "selected_count_at_judge_stop": selected_now,
                "batch_stats": batch_stats,
                "judge_protocol_version": _judge_protocol_version(backend),
                "judge_backend": backend,
                "judge_analysis_style": analysis_style,
                "judge_model": self.runtime.model,
                "direct_api_base_url": self.runtime.base_url,
                "api_key_configured": bool(self.runtime.api.api_key),
                "subagent_agent_id": (
                    self.runtime.subagent.agent_id if backend == "subagent" else ""
                ),
            },
            warnings=warnings,
        )

    def _close_active_analyzer(self) -> None:
        analyzer = self._active_analyzer
        self._active_analyzer = None
        close = getattr(analyzer, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - cleanup is operational hygiene.
                logger.warning(
                    "session judge analyzer cleanup failed; diagnosis result preserved",
                    error=f"{type(exc).__name__}: {exc}",
                )


class _SessionIdentityTracker:
    """Prevent the same physical Session from being submitted to OCSA twice.

    This tracker deliberately uses only a non-empty ``session_id``. It does not
    compare queries, tasks, failure labels, or OCSA outputs: every diagnosis
    returned by OCSA remains an independent eval-case candidate.
    """

    def __init__(self) -> None:
        self._reserved_session_ids: set[str] = set()

    @staticmethod
    def _session_id(row_or_diag: SessionRow | Diagnosis) -> str:
        row = row_or_diag.session if isinstance(row_or_diag, Diagnosis) else row_or_diag
        return str(row.session_id or "").strip()

    def duplicate_reason(self, row: SessionRow) -> str:
        session_id = self._session_id(row)
        if session_id and session_id in self._reserved_session_ids:
            return "same_session_id"
        return ""

    def reserve(self, row: SessionRow) -> None:
        session_id = self._session_id(row)
        if session_id:
            self._reserved_session_ids.add(session_id)

    def release(self, row_or_diag: SessionRow | Diagnosis) -> None:
        session_id = self._session_id(row_or_diag)
        if session_id:
            self._reserved_session_ids.discard(session_id)

    @staticmethod
    def stats(input_duplicates_skipped: int) -> dict[str, Any]:
        return {
            "enabled": True,
            "scope": "input_session_identity_only",
            "input_duplicate_skipped": input_duplicates_skipped,
            "post_ocsa_dedupe_enabled": False,
            "post_ocsa_duplicate_skipped": 0,
        }


def _diagnoses_include_nonretryable_auth_failure(
    diagnoses: list[Diagnosis],
) -> bool:
    """Return whether judge output reports a non-retryable auth failure."""

    error_symptoms = {
        "SESSION_JUDGE_ERROR",
        "OCSA_SESSION_REPORT_JUDGE_ERROR",
        "LLM_AS_JUDGE_ERROR",
    }
    auth_markers = (
        "http 401",
        "authenticationerror",
        "api key not found",
        "invalid api key",
        "unauthorized",
    )
    for diagnosis in diagnoses:
        if diagnosis.symptom_class not in error_symptoms:
            continue
        evidence_text = "\n".join(
            str(item.get("snippet") or "")
            for item in diagnosis.evidence
            if isinstance(item, dict)
        )
        error_text = f"{diagnosis.root_cause_summary}\n{evidence_text}".lower()
        if any(marker in error_text for marker in auth_markers):
            return True
    return False


def _equal_judge_chunks(
    rows: list[SessionRow], worker_count: int
) -> list[list[SessionRow]]:
    """Split rows into non-empty, near-equal chunks."""

    if not rows:
        return []
    effective_workers = max(1, min(worker_count, len(rows)))
    base_size, remainder = divmod(len(rows), effective_workers)
    chunks: list[list[SessionRow]] = []
    cursor = 0
    for index in range(effective_workers):
        chunk_size = base_size + (1 if index < remainder else 0)
        chunks.append(rows[cursor : cursor + chunk_size])
        cursor += chunk_size
    return chunks


def _empty_local_candidate_warning(
    all_rows: list[SessionRow],
    candidate_rows: list[SessionRow],
    filter_reasons: dict[str, int],
) -> str:
    """Explain why no local session reached the judge."""

    if not all_rows:
        return (
            "No local sessions were discovered from the configured local "
            "session roots; diagnose generated no cases."
        )
    if not candidate_rows:
        return (
            "Local sessions were discovered, but none remained after local "
            "candidate scoping."
        )
    if filter_reasons:
        return (
            "Local sessions were discovered, but none matched the requested "
            f"time/filter constraints. filter_reasons={dict(sorted(filter_reasons.items()))}"
        )
    return "Local sessions were discovered, but no candidates reached the session judge."


def _safe_runtime_error(
    exc: Exception, runtime: JudgeRuntimeConfig | LlmRuntimeConfig
) -> str:
    """Return a bounded runtime error with configured API credentials redacted."""

    api_key = (
        runtime.api.api_key
        if isinstance(runtime, JudgeRuntimeConfig)
        else runtime.api_key
    )
    return (
        f"{type(exc).__name__}: "
        f"{redact_secrets(str(exc), [api_key] if api_key else [])}"
    )[:500]


def _selected_count(
    diagnoses: list[Diagnosis], preference: CasePreference
) -> int:
    """Return the number of diagnoses selected under the active preference."""

    if not diagnoses:
        return 0
    return len(select_diagnoses(diagnoses, preference).selected)


def _rows_debug(rows: list[SessionRow], limit: int = 8) -> list[dict[str, Any]]:
    """Return bounded, non-secret session metadata for progress diagnostics."""

    summaries: list[dict[str, Any]] = []
    for row in rows[:limit]:
        query = " ".join(str(row.first_question or row.user_text or "").split())
        if len(query) > 160:
            query = query[:160] + f"...<truncated {len(query) - 160} chars>"
        summaries.append(
            {
                "session_id": row.session_id,
                "created_at": row.created_at,
                "path": row.path,
                "original_model": row.original_model,
                "first_question_preview": query,
            }
        )
    if len(rows) > limit:
        summaries.append({"remaining_rows_not_logged": len(rows) - limit})
    return summaries


def _scope_rows_to_local_candidates(
    rows: list[SessionRow], bot_id: str
) -> list[SessionRow]:
    """Return all auto-discovered local sessions.

    In diagnose local mode, session discovery already resolves the current
    OpenClaw runtime/session roots. Treat every discovered local session as an
    eval-candidate input for this diagnose run; do not drop rows again based on
    possibly missing/stale embedded bot_id fields. The bot_id parameter is
    intentionally unused and kept for API compatibility with older callers.
    """

    _ = bot_id
    return rows



def _filter_rows_by_preference_time(
    rows: list[SessionRow], preference: CasePreference
) -> list[SessionRow]:
    since = parse_session_datetime(preference.since)
    until = parse_session_datetime(preference.until)
    if not since and not until:
        return rows
    filtered: list[SessionRow] = []
    for row in rows:
        dt = session_row_datetime(row)
        if dt is None or (since and dt < since) or (until and dt > until):
            continue
        filtered.append(row)
    return filtered


def _sort_rows_newest(rows: list[SessionRow]) -> list[SessionRow]:
    return sorted(rows, key=_row_time_key, reverse=True)


def _row_time_key(row: SessionRow) -> tuple[float, str]:
    dt = session_row_datetime(row)
    return (dt.timestamp() if dt else 0.0, row.session_id or row.path)


def _selection_status(diagnoses: list[Diagnosis], preference: CasePreference) -> dict[str, Any]:
    result = select_diagnoses(diagnoses, preference) if diagnoses else None
    selected = result.selected if result else []
    selected_good = sum(d.case_type == "good" for d in selected)
    selected_bad = sum(d.case_type == "bad" for d in selected)
    required_good, required_bad = _required_case_type_counts(preference)
    return {
        "satisfied": (
            len(selected) >= preference.case_limit
            and selected_good >= required_good
            and selected_bad >= required_bad
        ),
        "selected_count": len(selected),
        "requested_count": preference.case_limit,
        "selected_good": selected_good,
        "required_good": required_good,
        "selected_bad": selected_bad,
        "required_bad": required_bad,
        "selection_report": result.report if result else {},
    }


def _remaining_judge_limit(
    selection_status: dict[str, Any], max_batch_size: int
) -> int:
    """Return the smallest useful next judge batch.

    The provider still uses parallelism while multiple cases are missing, but
    shrinks the final batch to the unresolved total/good/bad quota. This avoids
    launching up to a full extra batch after the user's request is nearly met.
    """

    deficits = (
        int(selection_status.get("requested_count", 0))
        - int(selection_status.get("selected_count", 0)),
        int(selection_status.get("required_good", 0))
        - int(selection_status.get("selected_good", 0)),
        int(selection_status.get("required_bad", 0))
        - int(selection_status.get("selected_bad", 0)),
    )
    unresolved = max(1, *(max(0, deficit) for deficit in deficits))
    return min(max(1, max_batch_size), unresolved)


def _required_case_type_counts(preference: CasePreference) -> tuple[int, int]:
    limit = max(1, preference.case_limit)
    if preference.good_case_count is not None:
        required_good = max(0, min(limit, preference.good_case_count))
    elif preference.include_good or preference.dataset_profile == "regression":
        required_good = min(limit, max(1 if limit >= 2 else 0, int(limit * preference.good_min_ratio)))
    else:
        required_good = 0
    if preference.bad_case_count is not None:
        required_bad = max(0, min(limit - required_good, preference.bad_case_count))
    else:
        required_bad = 1 if limit >= 2 and (preference.include_good or preference.bad_case_count is None) else 0
    return required_good, max(0, min(limit - required_good, required_bad))

def _progress(message: str, **fields: object) -> None:
    logger.info(message, **fields)
