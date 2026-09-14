from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .analysis_artifact import write_analysis_report
from .case_artifacts import write_case_artifacts
from .json_io import write_json
from .plan_handoff import write_plan_source
from .progress import progress
from .sampling import _diagnose_and_sample
from .summary import (
    _build_summary,
    _llm_preference_parse_enabled,
    _local_session_scan_limit,
)
from .. import logger
from ..acquisition.discovery import discover_layout
from ..acquisition.runtime_identity import resolve_runtime_bot_id
from ..acquisition.sessions import (
    discover_sessions,
    parse_jsonl_file,
    session_locator_from_path,
)
from ..acquisition.service_export import acquire_exported_sessions
from ..constants import DEFAULT_BASE_URL, DEFAULT_MAX_SESSIONS, DEFAULT_MODEL
from ..intent import parse_preference
from ..models import CasePreference, Diagnosis, LlmRuntimeConfig, RunRequest, RunResult
from ..product import DiagnosisOutcomeBuilder
from ..judge.runtime import judge_runtime_summary, resolve_judge_runtime
from ..utils import slugify


class JudgeExecutionFailedError(RuntimeError):
    """Raised when sessions were found but every semantic Judge call failed."""


def run_pipeline(req: RunRequest) -> RunResult:
    """Run the real diagnose pipeline.

    This runner owns real local acquisition + session judge + artifact generation.
    CLI parsing and ClawWeb lifecycle upload live outside this
    module so the production data path remains easy to inspect and test.
    """

    pipeline_started_at = time.time()
    out = req.output_dir
    out.mkdir(parents=True, exist_ok=True)
    logger.configure(out / "clawevolve-diagnose.log", secrets=[req.api_key])
    progress(
        "pipeline start",
        source=req.session_source or "local",
        output_dir=out,
        task_id=req.task_id or "",
        step_id=req.step_id or "",
        message_chars=len(req.message or ""),
        api_key_configured=bool(req.api_key),
        llm_base_url=req.llm_base_url or "code_default",
        model=req.model or DEFAULT_MODEL,
        max_sessions=req.max_sessions or "default",
        debug_session_path=req.debug_session_path or "",
        openclaw_home=req.openclaw_home or "~/.openclaw",
    )

    warnings: list[str] = []
    progress("runtime layout discovery start")
    layout = discover_layout(req.openclaw_home)
    progress(
        "runtime layout discovered",
        agent=layout.get("agent_id") or "unknown",
        openclaw_state=layout.get("openclaw_state") or "",
        workspace=layout.get("workspace") or "",
        session_dirs=len(layout.get("session_dirs", [])),
        session_dirs_preview=layout.get("session_dirs", [])[:8],
        discovery_elapsed_seconds=f"{time.time() - pipeline_started_at:.2f}",
    )

    direct_api_config = LlmRuntimeConfig(
        api_key=req.api_key,
        base_url=req.llm_base_url or DEFAULT_BASE_URL,
        model=req.model or DEFAULT_MODEL,
    )
    judge_runtime = resolve_judge_runtime(req)
    progress(
        "optional API runtime prepared",
        base_url=direct_api_config.base_url,
        model=direct_api_config.model,
        base_url_source="cli:--llm-base-url" if req.llm_base_url else "code_default",
        model_source="cli:--model" if req.model else "code_default",
        api_key_configured=bool(direct_api_config.api_key),
        used_for_judge=judge_runtime.backend == "api",
    )
    progress(
        "judge runtime resolved",
        **judge_runtime_summary(judge_runtime),
    )

    preference_parse_api_config = (
        direct_api_config
        if judge_runtime.backend == "api" and _llm_preference_parse_enabled()
        else LlmRuntimeConfig()
    )
    progress(
        "preference parse start",
        llm_preference_parse_enabled=bool(preference_parse_api_config.api_key),
        message_preview=(req.message or "")[:500],
    )
    pref, pref_warnings = parse_preference(req.message, preference_parse_api_config)
    _apply_session_analysis_limit(req, pref)
    warnings.extend(pref_warnings)
    progress(
        "preference parsed",
        case_limit=pref.case_limit,
        diagnosis_limit=pref.diagnosis_limit,
        target_modes=",".join(pref.normalized_modes()) or "mixed",
        include_good=pref.include_good,
        bad_case_count=pref.bad_case_count,
        good_case_count=pref.good_case_count,
        intent_preview=(pref.intent_text or "")[:300],
        intent_confidence=pref.intent_confidence,
        focus_terms=pref.focus_terms,
        required_terms=getattr(pref, "required_terms", []),
        excluded_terms=getattr(pref, "excluded_terms", []),
        time_range_label=pref.time_range_label,
        since=pref.since,
        until=pref.until,
        warning_count=len(pref_warnings),
        warnings=pref_warnings,
        analysis_session_limit=pref.max_sessions or "default",
    )

    max_sessions = _local_session_scan_limit(req, pref)
    source_bot_id = ""
    source_meta: dict[str, Any] = {}
    if req.session_source == "service_export":
        if req.debug_session_path:
            raise ValueError("--debug-session-path cannot be combined with --source service_export")
        progress(
            "service session acquisition start",
            scan_limit=max_sessions,
            source="clawweb_session_export",
        )
        acquired = acquire_exported_sessions(
            clawweb_url=req.clawweb_url,
            task_id=req.task_id,
            step_id=req.step_id,
            source_user_id=req.source_user_id,
            source_bot_id=req.source_bot_id,
            download_network=req.source_download_network,
            input_dir=out.parent / "input",
            max_sessions=max_sessions,
            since=pref.since,
            until=pref.until,
            parse_content=judge_runtime.backend == "api",
        )
        rows = acquired.rows
        source_bot_id = acquired.source_bot_id
        source_meta = acquired.source_metadata
        layout["diagnose_source"] = "service_session_export"
        layout["session_source"] = source_meta
        progress(
            "service session acquisition done",
            session_rows=len(rows),
            source_bot_id=source_bot_id,
            newest_session_id=rows[0].session_id if rows else "",
            oldest_session_id=rows[-1].session_id if rows else "",
        )
    elif req.debug_session_path:
        debug_path = Path(req.debug_session_path).expanduser().resolve(strict=False)
        progress(
            "debug single session load start",
            session_path=debug_path,
            exists=debug_path.exists(),
        )
        if not debug_path.exists():
            warnings.append(f"debug_session_path not found: {debug_path}")
        rows = (
            parse_jsonl_file(debug_path)
            if judge_runtime.backend == "api"
            else []
        )
        if not rows:
            locator = session_locator_from_path(debug_path)
            rows = [locator] if locator is not None else []
        if not pref.max_sessions:
            pref.max_sessions = 1
        progress(
            "debug single session load done",
            session_path=debug_path,
            session_rows=len(rows),
            effective_analysis_session_limit=pref.max_sessions,
            first_session_id=rows[0].session_id if rows else "",
            first_created_at=rows[0].created_at if rows else "",
        )
    else:
        progress("local session discovery start", scan_limit=max_sessions)
        rows = discover_sessions(
            layout,
            max_sessions,
            since=pref.since,
            until=pref.until,
            parse_content=judge_runtime.backend == "api",
        )
        progress(
            "local session discovery done",
            session_rows=len(rows),
            newest_session_id=rows[0].session_id if rows else "",
            newest_created_at=rows[0].created_at if rows else "",
            newest_path=rows[0].path if rows else "",
            oldest_session_id=rows[-1].session_id if rows else "",
            oldest_created_at=rows[-1].created_at if rows else "",
            oldest_path=rows[-1].path if rows else "",
        )

    bot_id, bot_meta = resolve_runtime_bot_id(source_bot_id, rows, layout)
    if source_meta:
        bot_meta = {**bot_meta, **source_meta}
    progress(
        "bot identity resolved",
        bot_id=bot_id,
        source=bot_meta.get("source"),
        metadata=bot_meta,
    )

    progress(
        "diagnosis sampling start",
        judge_order="chronological",
        session_rows=len(rows),
        bot_id=bot_id,
        case_limit=pref.case_limit,
        bad_case_count=pref.bad_case_count,
        good_case_count=pref.good_case_count,
        intent_preview=(pref.intent_text or "")[:300],
        intent_confidence=pref.intent_confidence,
    )
    diagnoses, selected, selection_report = _diagnose_and_sample(
        rows,
        bot_id,
        pref,
        layout,
        judge_runtime,
        out,
    )
    selection_report["diagnose_source"] = (
        "service_session_export"
        if req.session_source == "service_export"
        else "local"
    )
    raise_if_all_judge_assessments_failed(selection_report)
    warnings.extend(selection_report.get("judge_lookup_warnings", []))
    product_outcome = DiagnosisOutcomeBuilder().build(
        rows=rows,
        diagnoses=diagnoses,
        selected=selected,
        preference=pref,
        selection_report=selection_report,
    )
    safe_bot = slugify(bot_id, 80)
    progress(
        "diagnosis and selection done",
        diagnosed=len(diagnoses),
        selected=f"{len(selected)}/{pref.case_limit}",
        strategy=(selection_report.get("judge_lookup") or {}).get(
            "strategy", "unknown"
        ),
    )

    progress(
        "case artifact writing start",
        output_dir=out,
        safe_bot=safe_bot,
        diagnosis_count=len(diagnoses),
        selected_count=len(selected),
    )
    artifacts = write_case_artifacts(out, safe_bot, diagnoses, selected, pref)
    if source_meta:
        source_artifacts = source_meta.get("artifacts")
        if isinstance(source_artifacts, dict):
            artifacts.update(
                {
                    f"session_source_{key}": str(value)
                    for key, value in source_artifacts.items()
                    if str(value or "").strip()
                }
            )
    judge_dir = out / "judge"
    if judge_dir.is_dir():
        artifacts["judge_dir"] = str(judge_dir)
    artifacts["log_file"] = logger.log_path()
    progress("case artifacts written", artifacts=artifacts)

    progress(
        "analysis report writing start",
        selected_count=len(selected),
        selection_status=selection_report.get("status"),
    )
    report_path = write_analysis_report(
        out,
        safe_bot,
        bot_id,
        selected,
        selection_report,
        layout,
        rows=rows,
        preference=pref,
        product_outcome=product_outcome,
    )
    artifacts["analysis_report_md"] = str(report_path)
    progress("analysis report written", path=report_path)

    progress(
        "plan source writing start",
        selected_count=len(selected),
        diagnose_result_json=artifacts.get("diagnose_result_json", ""),
    )
    plan_path = write_plan_source(
        out=out,
        safe_bot=safe_bot,
        bot_id=bot_id,
        task_id=req.task_id,
        artifacts=artifacts,
        layout=layout,
        pref=pref,
        selected=selected,
        selection_report=selection_report,
    )
    artifacts["plan_source_json"] = str(plan_path)
    progress("plan source written", path=plan_path)

    _append_underfill_warnings(warnings, selected, pref, selection_report)
    progress(
        "warnings finalized",
        warning_count=len(warnings),
        warnings=warnings,
    )

    progress(
        "summary build start",
        selected_count=len(selected),
        diagnosis_count=len(diagnoses),
    )
    summary = _build_summary(
        bot_id=bot_id,
        rows=rows,
        diagnoses=diagnoses,
        selected=selected,
        selection_report=selection_report,
        bot_meta=bot_meta,
        direct_api_config=direct_api_config,
        judge_runtime=judge_runtime,
        pref=pref,
        artifacts=artifacts,
        warnings=warnings,
        req=req,
        product_outcome=product_outcome,
    )
    summary_path = out / f"{safe_bot}_summary.json"
    _finalize_summary(summary, summary_path, out, req, selected, artifacts)
    progress(
        "summary finalized",
        summary_path=summary_path,
        ready_for_plan=summary.get("ready_for_plan"),
        agent_next_action=summary.get("agent_next_action"),
    )
    write_json(summary_path, summary)
    progress(
        "pipeline done",
        summary=summary_path,
        plan_source=artifacts.get("plan_source_json", ""),
        log_file=logger.log_path(),
        elapsed_seconds=f"{time.time() - pipeline_started_at:.2f}",
    )
    return RunResult(summary_path=summary_path, summary=summary)


def raise_if_all_judge_assessments_failed(selection_report: dict[str, Any]) -> None:
    """Keep a real zero-case result distinct from total Judge unavailability."""

    judge_lookup = selection_report.get("judge_lookup") or {}
    if not isinstance(judge_lookup, dict) or not judge_lookup.get(
        "all_judge_assessments_failed"
    ):
        return
    judged = int(judge_lookup.get("judged_session_count") or 0)
    failed = int(judge_lookup.get("failed_session_count") or 0)
    backend = str(judge_lookup.get("judge_backend") or "unknown")
    stop_reason = str(judge_lookup.get("judge_stop_reason") or "unknown")
    samples = judge_lookup.get("judge_failure_samples") or []
    if not isinstance(samples, list):
        samples = [samples]
    detail = next((str(item).strip() for item in samples if str(item).strip()), "")
    message = (
        f"JUDGE_EXECUTION_FAILED: all session judge assessments failed "
        f"({failed}/{judged}); backend={backend}; stop_reason={stop_reason}"
    )
    if detail:
        message += f"; detail={detail}"
    raise JudgeExecutionFailedError(message)


def _apply_session_analysis_limit(req: RunRequest, pref: CasePreference) -> None:
    """Resolve the shared post-filter Judge budget in one place."""

    if req.max_sessions and req.max_sessions > 0:
        pref.max_sessions = req.max_sessions
    elif not pref.max_sessions or pref.max_sessions <= 0:
        # Apply one shared default to personal/service Bots and API/Subagent
        # judges. Discovery performs cheap filters first and returns the newest
        # rows within this budget; only those rows may enter LLM Judge.
        pref.max_sessions = DEFAULT_MAX_SESSIONS


def _append_underfill_warnings(
    warnings: list[str],
    selected: list[Diagnosis],
    pref: CasePreference,
    selection_report: dict[str, object],
) -> None:
    if len(selected) < pref.case_limit:
        judge_lookup = selection_report.get("judge_lookup", {})
        strategy = (
            judge_lookup.get("strategy", "local session analysis")
            if isinstance(judge_lookup, dict)
            else "local session analysis"
        )
        stop_reason = (
            judge_lookup.get("judge_stop_reason", "")
            if isinstance(judge_lookup, dict)
            else ""
        )
        if stop_reason == "judge_auth_failed":
            warnings.append(
                f"Selected {len(selected)}/{pref.case_limit} cases because local API judging stopped on authentication failure; "
                "ensure the diagnose process receives the intended OPENAI_API_KEY/base URL/model and rerun before using plan."
            )
        else:
            warnings.append(
                f"Only selected {len(selected)} cases out of requested {pref.case_limit} "
                f"after scanning local sessions with {strategy}; qualified, deduplicated "
                "candidate pool was insufficient under the current filters/coverage."
            )
    quota_underfilled = selection_report.get("quota_underfilled") or {}
    if quota_underfilled:
        warnings.append(
            "Selected case total may be full, but explicit good/bad distribution is underfilled: "
            f"{quota_underfilled}. The run exhausted the configured local scan/judge candidate window."
        )


def _finalize_summary(
    summary: dict[str, object],
    summary_path: object,
    out: object,
    req: RunRequest,
    selected: list[Diagnosis],
    artifacts: dict[str, object],
) -> None:
    summary["summary_json"] = str(summary_path)
    summary["run_dir"] = str(out)
    summary["task_id"] = req.task_id
    summary["step_id"] = req.step_id
    summary["next_step"] = f"/clawevolve-plan --task-id {req.task_id} --run-dir {out}"
    diagnosis_status = str(summary.get("diagnosis_status") or "")
    ready_for_plan = (
        len(selected) > 0
        and bool(artifacts.get("plan_source_json"))
        and diagnosis_status != "insufficient_evidence"
    )
    summary["ready_for_plan"] = ready_for_plan
    summary["agent_next_action"] = "run_plan" if ready_for_plan else "inspect_warnings"
    summary["review_artifacts"] = {
        "analysis_report_md": artifacts.get("analysis_report_md", ""),
        "plan_source_json": artifacts.get("plan_source_json", ""),
        "diagnose_cases_dir": artifacts.get("diagnose_cases_dir", ""),
    }
