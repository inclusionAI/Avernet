from __future__ import annotations

from typing import Any, Callable

from .. import logger as diag_logger

StepReporter = Callable[..., dict[str, Any]]


def post_success_report(step_reporter: StepReporter, *, task_id: str, step_id: str, summary: str, output: dict[str, Any]) -> dict[str, Any]:
    diag_logger.info(
        "clawweb final report start",
        task_id=task_id,
        step_id=step_id,
        status="succeeded",
        output_keys=sorted(output.keys()) if isinstance(output, dict) else [],
        report_policy="final_only",
        case_total=((output.get("cases") or {}).get("total") if isinstance(output, dict) else None),
        good_count=((output.get("cases") or {}).get("goodCount") if isinstance(output, dict) else None),
        bad_count=((output.get("cases") or {}).get("badCount") if isinstance(output, dict) else None),
    )
    result = step_reporter(task_id, step_id, status="succeeded", summary=summary, output=output)
    log_report_result("clawweb final report result", result)
    return result


def post_failure_report(
    step_reporter: StepReporter,
    *,
    task_id: str,
    step_id: str,
    summary: str,
    error: str,
    error_code: str = "DIAGNOSE_PIPELINE_FAILED",
) -> dict[str, Any]:
    diag_logger.info(
        "clawweb failure report start",
        task_id=task_id,
        step_id=step_id,
        status="failed",
        report_policy="final_only",
        error_preview=str(error or "")[:500],
    )
    result = step_reporter(
        task_id,
        step_id,
        status="failed",
        summary=summary,
        error={
            "code": error_code,
            "message": str(error or "Diagnose pipeline failed")[:4000],
            "retryable": True,
        },
    )
    log_report_result("clawweb failure report result", result)
    return result


def log_report_result(message: str, result: dict[str, Any]) -> None:
    diag_logger.info(
        message,
        status=result.get("status") if isinstance(result, dict) else None,
        http_status=result.get("http_status") if isinstance(result, dict) else None,
        attempts=result.get("attempts") if isinstance(result, dict) else None,
        report_task_id=result.get("task_id") if isinstance(result, dict) else None,
        report_step_id=result.get("step_id") if isinstance(result, dict) else None,
        url=result.get("url") if isinstance(result, dict) else None,
        report_status=result.get("report_status") if isinstance(result, dict) else None,
        error_category=result.get("error_category") if isinstance(result, dict) else None,
        likely_reason=result.get("likely_reason") if isinstance(result, dict) else None,
        retryable=result.get("retryable") if isinstance(result, dict) else None,
        error=result.get("error") if isinstance(result, dict) else None,
    )
