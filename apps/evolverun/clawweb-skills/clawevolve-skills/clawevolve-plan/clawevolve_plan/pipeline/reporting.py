from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, Callable

from .. import logger
from .inputs import _archived_input_path
from .step_report_payload import build_step_report_output as _step_report_output
from .step_report_payload import (
    existing_step_report_output as _existing_step_report_output,
)
from .upload import upload_result_is_complete


StepReporter = Callable[..., dict[str, Any]]


class FinalStepReportGate:
    """Allow at most one terminal report attempt for a Plan command.

    The report may already have advanced the ClawWeb workflow when a later
    local operation fails (for example, persisting the response on a full
    filesystem).  A command-level exception handler must not then send a
    contradictory second terminal report.
    """

    def __init__(self, reporter: StepReporter) -> None:
        self._reporter = reporter
        self._lock = Lock()
        self._attempted = False
        self._result: dict[str, Any] | None = None

    @property
    def attempted(self) -> bool:
        with self._lock:
            return self._attempted

    @property
    def result(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._result) if isinstance(self._result, dict) else None

    def __call__(self, task_id: str, step_id: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            if self._attempted:
                logger.warning(
                    "duplicate terminal step report suppressed",
                    task_id=task_id,
                    step_id=step_id,
                    requested_status=payload.get("status"),
                )
                return {
                    "status": "suppressed",
                    "reason": "terminal_report_already_attempted",
                    "original_result": dict(self._result)
                    if isinstance(self._result, dict)
                    else None,
                }
            self._attempted = True
        try:
            raw_result = self._reporter(task_id, step_id, **payload)
        except Exception as exc:  # noqa: BLE001 - retain the terminal attempt.
            result = _reporter_exception_result(exc)
            logger.error(
                "clawweb final step reporter raised",
                task_id=task_id,
                step_id=step_id,
                error=result["error"],
            )
        else:
            result = _normalize_reporter_result(raw_result)
        with self._lock:
            self._result = dict(result)
        return result


def _reporter_exception_result(exc: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
        "error_category": "step_reporter_exception",
    }


def _normalize_reporter_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return {
        "status": "error",
        "error": "step reporter returned a non-object result",
        "error_category": "invalid_step_reporter_result",
    }


def submit_final_step_report(
    step_reporter: StepReporter,
    task_id: str,
    step_id: str,
    **payload: Any,
) -> dict[str, Any]:
    """Invoke the only terminal step report without leaking reporter exceptions.

    A transport adapter is expected to return a structured deferred/error result,
    but this boundary also guards unexpected adapter exceptions.  Keeping the
    exception local prevents the command-level failure handler from issuing a
    second terminal report for the same Plan run.
    """

    try:
        result = step_reporter(task_id, step_id, **payload)
    except Exception as exc:  # noqa: BLE001 - preserve final-only reporting.
        normalized = _reporter_exception_result(exc)
        logger.error(
            "clawweb final step reporter raised",
            task_id=task_id,
            step_id=step_id,
            error=normalized["error"],
        )
        return normalized
    return _normalize_reporter_result(result)


def _report_log_fields(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"status": "not_attempted"}
    fields = {
        "status": result.get("status"),
        "http_status": result.get("http_status"),
        "attempts": result.get("attempts"),
        "url": result.get("url"),
        "error": result.get("error"),
        "error_category": result.get("error_category"),
        "auth_gate_reason": result.get("auth_gate_reason"),
        "likely_reason": result.get("likely_reason"),
        "response_preview": result.get("response_preview"),
        "payload_preview": result.get("payload_preview"),
        "response_headers": result.get("response_headers"),
    }
    return {k: v for k, v in fields.items() if v not in (None, "", {})}


def _log_step_report_done(
    message: str, *, phase: str, result: dict[str, Any] | None
) -> None:
    logger.info(message, phase=phase, **_report_log_fields(result))


def final_step_report_is_complete(clawweb_step_report: dict[str, Any]) -> bool:
    final = clawweb_step_report.get("final") or {}
    return isinstance(final, dict) and str(final.get("status") or "").lower() == "ok"


def _result(
    spec: dict[str, Any],
    objective_md_path: Path,
    objective_json_path: Path,
    spec_md_path: Path,
    spec_json_path: Path,
    template_dir: Path,
    zip_path: Path,
    upload_result: dict[str, Any],
    oss_upload_result: dict[str, Any],
    input_archive: dict[str, Any],
    clawweb_step_report: dict[str, Any],
) -> dict[str, Any]:
    upload_ready = upload_result_is_complete(upload_result)
    required = bool(upload_result.get("required", True))
    report_ready = final_step_report_is_complete(clawweb_step_report)
    ready = not required or (upload_ready and report_ready)
    if ready:
        next_action = "start_patch_loop"
    elif upload_ready:
        next_action = "retry_clawweb_report"
    else:
        next_action = "retry_clawweb_upload"
    return {
        "status": "ok" if ready else "error",
        "ready_for_patch_loop": ready,
        "agent_next_action": next_action,
        "objective_md": str(objective_md_path),
        "objective_json": str(objective_json_path),
        "spec_md": str(spec_md_path),
        "spec_json": str(spec_json_path),
        "template_dir": str(template_dir),
        "zip_path": str(zip_path),
        "template_count": len(list(template_dir.glob("**/task_*.md")))
        if template_dir.exists()
        else 0,
        "output_dir": str(objective_md_path.parent),
        "clawweb_upload_result": upload_result,
        "clawweb_step_report": clawweb_step_report,
        "oss_upload_result": oss_upload_result,
        "input_archive": input_archive,
        "input_dir": input_archive.get("input_dir", ""),
        "discovery_notes": _archived_input_path(input_archive, "discovery_notes")
        or _archived_input_path(input_archive, "discovery_notes_inline"),
        "input_manifest": str(
            Path(str(input_archive.get("output_dir") or objective_md_path.parent))
            / "input_manifest.json"
        ),
        "log_file": logger.log_path(),
        "clawweb_domain": spec["deliverables"].get("clawweb_domain", {}),
        "active_optimization_directions": spec.get(
            "active_optimization_directions", []
        ),
        "failure_modes_to_address": spec.get("failure_modes_to_address", []),
        "allowed_update_targets": spec.get("allowed_update_targets", []),
        "root_cause_count": len(spec.get("root_cause_clusters", [])),
    }


__all__ = [
    "_existing_step_report_output",
    "_log_step_report_done",
    "_result",
    "_step_report_output",
    "FinalStepReportGate",
    "final_step_report_is_complete",
    "submit_final_step_report",
]
