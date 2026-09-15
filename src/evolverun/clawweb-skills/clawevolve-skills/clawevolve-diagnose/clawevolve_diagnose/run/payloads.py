from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def error_payload(source: Any, safe_error: str, *, clawweb_upload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "status": "error",
        "ready_for_plan": False,
        "agent_next_action": "inspect_warnings",
        "source": "local",
        "task_id": getattr(source, "task_id", ""),
        "step_id": getattr(source, "step_id", ""),
        "error": safe_error,
        "warnings": [safe_error],
        "clawweb_upload": clawweb_upload,
    }


def success_payload(result: Any) -> dict[str, Any]:
    artifacts = result.summary.get("artifacts") or {}
    selection_report = result.summary.get("selection_report") or {}
    judge_lookup = selection_report.get("judge_lookup") or {}
    return {
        "status": "ok",
        "summary_json": str(result.summary_path),
        "run_dir": result.summary.get("run_dir") or str(Path(result.summary_path).parent),
        "task_id": result.summary.get("task_id", ""),
        "step_id": result.summary.get("step_id", ""),
        "bot_id": result.summary.get("bot_id"),
        "source": result.summary.get("source"),
        "analysis_strategy": judge_lookup.get("strategy", ""),
        "ready_for_plan": result.summary.get("ready_for_plan", False),
        "agent_next_action": result.summary.get("agent_next_action", ""),
        "discovered_session_count": result.summary.get("discovered_session_count"),
        "diagnosis_count": result.summary.get("diagnosis_count"),
        "candidate_acquisition": judge_lookup.get("candidate_acquisition", {}),
        "requested_case_count": result.summary.get("requested_case_count"),
        "eval_query_count": result.summary.get("eval_query_count"),
        "diagnose_cases_dir": artifacts.get("diagnose_cases_dir", ""),
        "diagnose_result_json": artifacts.get("diagnose_result_json", ""),
        "analysis_report_md": artifacts.get("analysis_report_md", ""),
        "plan_source_json": artifacts.get("plan_source_json", ""),
        "review_artifacts": result.summary.get("review_artifacts", {}),
        "clawweb_upload": result.summary.get("clawweb_upload"),
        "next_step": result.summary.get("next_step") or f"/clawevolve-plan --run-dir {Path(result.summary_path).parent}",
        "log_file": result.summary.get("log_file") or artifacts.get("log_file", ""),
        "warnings": result.summary.get("warnings", []),
    }


def rewrite_summary_with_upload(result: Any) -> None:
    try:
        result.summary_path.write_text(json.dumps(result.summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
