from __future__ import annotations

from collections import Counter
from typing import Any

from ..constants import DEFAULT_MAX_SESSIONS, DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
from ..judge.openai_chat_client import DEFAULT_LLM_HTTP_RETRIES
from ..judge.ocsa_contract import OCSA_ANALYSIS_STYLE, OCSA_PROTOCOL_VERSION
from ..judge.keyless_session_prompt import OUTPUT_SCHEMA_VERSION
from ..judge.local_session_judge_provider import (
    DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS,
    DEFAULT_MAX_JUDGE_ROUNDS,
    DEFAULT_JUDGE_SESSION_BATCH_SIZE,
)
from ..judge.runtime import judge_runtime_summary, resolve_judge_runtime
from ..models import (
    CasePreference,
    Diagnosis,
    JudgeRuntimeConfig,
    LlmRuntimeConfig,
    RunRequest,
    SessionRow,
)
from ..utils import utc_iso


def _build_summary(
    bot_id: str,
    rows: list[SessionRow],
    diagnoses: list[Diagnosis],
    selected: list[Diagnosis],
    selection_report: dict[str, Any],
    bot_meta: dict[str, Any],
    direct_api_config: LlmRuntimeConfig,
    judge_runtime: JudgeRuntimeConfig,
    pref: CasePreference,
    artifacts: dict[str, str],
    warnings: list[str],
    req: RunRequest | None = None,
    product_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity_meta = {"bot_id": bot_meta}
    return {
        "schema_version": "clawevolve-diagnose-summary.v1",
        "source": req.session_source if req else "local",
        "task_id": req.task_id if req else "",
        "step_id": req.step_id if req else "",
        "generated_at": utc_iso(),
        "bot_id": bot_id,
        "discovered_session_count": len(rows),
        "diagnosis_count": len(diagnoses),
        "requested_case_count": pref.case_limit,
        "eval_query_count": len(selected),
        "case_distribution": _case_distribution(selected),
        "selection_report": selection_report,
        "selection_coverage": _selection_coverage(rows, diagnoses, selected),
        "template_dir": "",
        "zip_path": "",
        "template_names": [],
        "clawweb_upload": {"enabled": True, "status": "pending_final_event"},
        "runtime_identity": identity_meta,
        "direct_api_llm": direct_api_config.safe_summary(),
        "judge_runtime": judge_runtime_summary(judge_runtime),
        "effective_parameters": _effective_parameters(
            pref, direct_api_config, req, judge_runtime
        ),
        "artifacts": artifacts,
        "log_file": artifacts.get("log_file", ""),
        "summary_json": "",
        "warnings": warnings,
        **(product_outcome or {}),
    }


def _effective_parameters(
    pref: CasePreference,
    direct_api_config: LlmRuntimeConfig,
    req: RunRequest | None = None,
    judge_runtime: JudgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    """Expose the parameters that actually affect this diagnose run.

    This is intentionally key-safe and delivery-facing: reviewers should be able
    to verify from summary.json whether a user option or code default changed discovery,
    batching, judge, selection, dedupe, or only downstream plan metadata.
    """

    local_session_scan_limit = _local_session_scan_limit(req, pref)
    analysis_session_limit = _analysis_session_limit(req, pref)
    effective_judge_runtime = judge_runtime or (
        resolve_judge_runtime(req) if req else JudgeRuntimeConfig()
    )
    judge_summary = judge_runtime_summary(effective_judge_runtime)
    return {
        "output": {
            "task_id": req.task_id if req else "",
            "step_id": req.step_id if req else "",
            "task_id_source": "cli:--task-id" if req and req.task_id else "missing",
            "step_id_source": "cli:--step-id" if req and req.step_id else "missing",
            "output_dir": str(req.output_dir) if req else "",
            "debug_session_path": req.debug_session_path if req else "",
        },
        "data_source": {
            "source": req.session_source if req else "local",
            "source_user_switchable": False,
            "debug_single_session": bool(req and req.debug_session_path),
            "odps_supported": False,
        },
        "judge_chain": {
            **direct_api_config.safe_summary(),
            "api_key_required": judge_summary["backend"] == "api",
            "api_key_persisted": False,
            "judge_backend": judge_summary["backend"],
            "judge_analysis_style": judge_summary["analysis_style"],
            "judge_selection_policy": judge_summary["selection_policy"],
            "subagent_agent_id": (judge_summary.get("subagent") or {}).get(
                "agent_id", ""
            ),
            "used_for": [
                "api_only:ocsa_session_report_with_optional_request_matcher",
                "deterministic_preference_parse_only",
            ],
        },
        "session_discovery": {
            "local_session_scan_limit": local_session_scan_limit,
            "analysis_session_limit": analysis_session_limit,
            "max_sessions_semantics": "actual_judged_sessions_not_discovered_rows",
            "max_sessions_source": _max_sessions_source(req, pref),
            "primary_source": (
                "clawweb_service_session_export_manifest"
                if req and req.session_source == "service_export"
                else "openclaw_sessions_json"
            ),
            "primary_time_field": (
                "transcript_event_time"
                if req and req.session_source == "service_export"
                else "sessionStartedAt"
            ),
            "store_entry_filter": "top_level_user_session_entries_only",
            "jsonl_scan_fallback": "only_when_sessions_json_is_missing",
            "session_file_scan_limit": _default_int(
                100000
                if (pref.since or pref.until)
                else max(100, local_session_scan_limit * 4)
            ),
            "scan_max_dirs": _default_int(20000),
            "scan_max_files": _default_int(100000),
            "raw_text_limit": _default_int(12000),
            "event_head_limit": _default_int(80),
            "event_tail_limit": _default_int(240),
            "order": "sessions_json_time_window_first_then_sessionStartedAt_desc",
        },
        "batching_and_judge": _effective_batching_and_judge_parameters(
            req, effective_judge_runtime
        ),
        "selection_preference": {
            "diagnosis_mode": pref.diagnosis_mode,
            "hypothesis_text": pref.hypothesis_text,
            "case_limit": pref.case_limit,
            "include_good": pref.include_good,
            "bad_case_count": pref.bad_case_count,
            "good_case_count": pref.good_case_count,
            "dataset_profile": pref.dataset_profile,
            "target_failure_modes": pref.normalized_modes(),
            "focus_terms": pref.focus_terms,
            "required_terms": pref.required_terms,
            "excluded_terms": pref.excluded_terms,
            "since": pref.since,
            "until": pref.until,
            "time_range_label": pref.time_range_label,
            "message_max_sessions": pref.max_sessions,
            "drop_context_dependent": pref.drop_context_dependent,
            "require_diversity": pref.require_diversity,
        },
        "dedupe": {
            "enabled": True,
            "scope": "input_session_identity_only",
            "policy": "same_non_empty_session_id_before_ocsa",
            "post_ocsa_dedupe_enabled": False,
        },
        "downstream_plan_metadata_only": {
            "case_timeout_seconds": pref.timeout_seconds,
            "rewrite_multi_sentence_query": pref.rewrite_multi_sentence_query,
            "search_evidence_required_for_high_score": (
                pref.search_evidence_required_for_high_score
            ),
            "stable_run_required": pref.stable_run_required,
            "diagnosis_limit": pref.diagnosis_limit,
        },
    }


def _analysis_session_limit(
    req: RunRequest | None, pref: CasePreference | None
) -> int | None:
    if req is not None and req.max_sessions and req.max_sessions > 0:
        return req.max_sessions
    if pref is not None and pref.max_sessions and pref.max_sessions > 0:
        return pref.max_sessions
    return None


def _max_sessions_source(req: RunRequest | None, pref: CasePreference | None) -> str:
    if req is not None and req.max_sessions and req.max_sessions > 0:
        return "cli:--max-sessions"
    if pref is not None and pref.max_sessions and pref.max_sessions > 0:
        return "message"
    return "default"


def _effective_batching_and_judge_parameters(
    req: RunRequest | None, judge_runtime: JudgeRuntimeConfig | None = None
) -> dict[str, Any]:
    backend = judge_runtime.backend if judge_runtime else "api"
    max_concurrent = (
        1 if backend == "subagent" else DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS
    )
    return {
        "judge_order": "chronological",
        "judge_backend": backend,
        "judge_analysis_style": (
            OCSA_ANALYSIS_STYLE
            if backend == "api"
            else "diagnose_native_single_session_llm"
        ),
        "session_batch_size": _effective_session_batch_size(req),
        "per_batch_judge_limit": _effective_judge_limit(req),
        "max_judge_rounds": _default_int(DEFAULT_MAX_JUDGE_ROUNDS),
        "max_concurrent_tasks": _default_int(max_concurrent),
        "judge_call_timeout_seconds": _default_int(
            DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
        ),
        "llm_retry_owner": "diagnose_http_client",
        "llm_call_retries": _default_int(DEFAULT_LLM_HTTP_RETRIES),
        "llm_call_attempts": _default_int(1 + DEFAULT_LLM_HTTP_RETRIES),
        "judge_protocol_version": (
            OCSA_PROTOCOL_VERSION if backend == "api" else OUTPUT_SCHEMA_VERSION
        ),
    }


def _effective_session_batch_size(req: RunRequest | None) -> int:
    return _default_int(DEFAULT_JUDGE_SESSION_BATCH_SIZE)


def _effective_judge_limit(req: RunRequest | None) -> int:
    return _default_int(DEFAULT_JUDGE_SESSION_BATCH_SIZE)


def _case_distribution(selected: list[Diagnosis]) -> dict[str, dict[str, int]]:
    return {
        "by_case_type": dict(Counter(d.case_type for d in selected)),
        "by_failure_mode": dict(Counter(d.evolution_failure_mode for d in selected)),
        "by_original_model": dict(
            Counter(d.session.original_model or "unknown" for d in selected)
        ),
    }


def _selection_coverage(
    rows: list[SessionRow], diagnoses: list[Diagnosis], selected: list[Diagnosis]
) -> dict[str, Any]:
    return {
        "scanned_session_count": len(rows),
        "diagnosed_session_count": len(diagnoses),
        "selected_session_count": len(selected),
        "diagnosed_by_case_type": dict(Counter(d.case_type for d in diagnoses)),
        "diagnosed_by_failure_mode": dict(
            Counter(d.evolution_failure_mode for d in diagnoses)
        ),
    }


def _max_sessions_default() -> int:
    return DEFAULT_MAX_SESSIONS


def _local_session_scan_limit(
    req: RunRequest | None = None, pref: CasePreference | None = None
) -> int:
    """Return the local discovery scan cap.

    ``--max-sessions`` / message ``max_sessions`` means the number of sessions
    retained after cheap discovery filters and sent toward judge analysis.
    Discovery applies the requested time window before this newest-first cap,
    so it does not need to parse a wider transcript window.
    """

    analysis_limit = _analysis_session_limit(req, pref)
    if analysis_limit and analysis_limit > 0:
        return min(analysis_limit, 5000)
    return _max_sessions_default()


def _default_int(default: int) -> int:
    return default


def _llm_preference_parse_enabled() -> bool:
    """Use the configured direct API runtime to parse natural-language diagnose requests."""

    return True
