"""Snapshot helpers for service-Bot standard evaluation tasks."""

from __future__ import annotations

from typing import Any


def public_standard_environment(environment: dict[str, Any]) -> dict[str, Any]:
    """Hide routing headers, device IDs, and infrastructure bindings from clients."""
    return {
        "supported": bool(environment.get("supported", True)),
        "default_tag": str(environment.get("default_tag") or "eval"),
        "build_status": str(environment.get("build_status") or ""),
        "build_info": str(environment.get("build_info") or ""),
        "error": str(environment.get("error") or ""),
    }


def standard_task_snapshot(record: Any) -> dict[str, Any]:
    ext = dict(record.ext) if isinstance(record.ext, dict) else {}

    eval_progress = ext.get("eval_progress") if isinstance(ext.get("eval_progress"), dict) else {}
    environment = ext.get("standard_environment") if isinstance(ext.get("standard_environment"), dict) else {}
    progress_status = str(eval_progress.get("status") or "")
    task_status = str(record.status)
    report = eval_progress.get("report") if isinstance(eval_progress.get("report"), dict) else {}
    report_url = str(report.get("reportUrl") or "")

    has_started_eval = bool(ext.get("set_task_uuid") or eval_progress)
    build_status = str(environment.get("build_status") or "").lower()
    if environment.get("supported") and build_status == "active":
        env_state = "DONE"
    elif environment.get("supported") and build_status in {"failed", "not_found"}:
        env_state = "FAILED"
    elif environment.get("supported") and build_status in {"building", "preparing", "running"}:
        env_state = "RUNNING"
    elif task_status in {"init", "env_preparing"}:
        env_state = "RUNNING"
    elif task_status in {"env_ready", "task_created", "task_executed"}:
        env_state = "DONE"
    elif task_status in {"success", "failed"} and has_started_eval:
        env_state = "DONE"
    elif task_status == "success":
        env_state = "DONE"
    elif task_status == "failed":
        env_state = "FAILED"
    else:
        env_state = "UNKNOWN"

    known_statuses = {"init", "env_preparing", "env_ready", "task_created", "task_executed", "success", "failed"}
    if env_state == "UNKNOWN" or task_status not in known_statuses:
        sample_state = "UNKNOWN"
    elif env_state == "DONE" or has_started_eval:
        sample_state = "DONE"
    else:
        sample_state = "WAITING"

    if task_status in {"init", "env_preparing"}:
        eval_state = "WAITING"
    elif task_status in {"env_ready", "task_created"}:
        eval_state = "RUNNING"
    elif task_status in {"task_executed", "success"}:
        eval_state = "DONE"
    elif task_status == "failed" and has_started_eval:
        eval_state = "FAILED"
    elif task_status == "failed":
        eval_state = "WAITING"
    else:
        eval_state = "UNKNOWN"

    if report_url:
        report_state = "DONE"
    elif task_status == "task_executed":
        report_state = "RUNNING"
    elif task_status in {"init", "env_preparing", "env_ready", "task_created"}:
        report_state = "WAITING"
    elif eval_state in {"DONE", "FAILED"}:
        report_state = "FAILED"
    elif eval_state == "UNKNOWN":
        report_state = "UNKNOWN"
    else:
        report_state = "WAITING"

    save_state = "WAITING"
    save_status = str(ext.get("standard_result_save_status") or "").upper()
    if task_status in {"success", "failed"}:
        if ext.get("standard_result_saved") is True or save_status in {"SAVED", "SKIPPED", "DONE"}:
            save_state = "DONE"
        elif save_status in {"FAILED", "ERROR"}:
            save_state = "FAILED"
        else:
            save_state = "RUNNING"

    return {
        "id": record.id,
        "task_id": record.id,
        "uuid": record.uuid,
        "task_type": record.task_type,
        "biz_type": record.biz_type,
        "status": task_status,
        "source_status": ext.get("source_status"),
        "set_uuid": ext.get("set_uuid"),
        "set_name": ext.get("sample_set_name"),
        "sample_count": ext.get("sample_set_count"),
        "set_task_uuid": ext.get("set_task_uuid"),
        "environment": public_standard_environment(environment),
        "environment_status": environment.get("build_status"),
        "progress": eval_progress,
        "progress_status": progress_status,
        "report_url": report_url,
        "error": ext.get("error") or ext.get("error_msg") or eval_progress.get("error") or "",
        "steps": [
            {
                "key": "create_environment",
                "status": env_state,
                "error": str(environment.get("error") or "") if env_state == "FAILED" else "",
            },
            {"key": "confirm_sample_set", "status": sample_state, "error": ""},
            {
                "key": "execute_evaluation",
                "status": eval_state,
                "error": str(ext.get("error_msg") or eval_progress.get("error") or "") if eval_state == "FAILED" else "",
            },
            {
                "key": "evaluation_report",
                "status": report_state,
                "error": (
                    ""
                    if report_url
                    else str(
                        ext.get("error_msg")
                        or eval_progress.get("error")
                        or ("评测失败，未生成报告" if task_status == "failed" else "MASA未返回评测报告地址")
                    )
                ) if report_state == "FAILED" else "",
            },
            {
                "key": "save_evaluation_result",
                "status": save_state,
                "error": "评测结果保存失败，请重试" if save_state == "FAILED" else "",
            },
        ],
    }
