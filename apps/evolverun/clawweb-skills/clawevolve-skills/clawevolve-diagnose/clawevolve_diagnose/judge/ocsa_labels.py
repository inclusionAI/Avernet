from __future__ import annotations

from typing import Any


_SUCCESS_LABELS = {"", "COMPLETED", "SUCCESS", "OK", "NONE", "NULL"}
_UNKNOWN_LABELS = {"UNKNOWN", "UNCLEAR"}


def ocsa_task_failure_label(task: dict[str, Any]) -> str:
    """Return OCSA's task-level label without translating its taxonomy."""

    value = str(task.get("task_failure_class") or "").strip().upper()
    if value:
        return value
    return "COMPLETED" if ocsa_task_completed(task) else "UNKNOWN"


def ocsa_error_labels(task: dict[str, Any]) -> list[str]:
    """Collect original OCSA task/tool labels in stable evidence order."""

    labels: list[str] = []
    task_label = ocsa_task_failure_label(task)
    if task_label not in _SUCCESS_LABELS:
        labels.append(task_label)
    for tool_type in ("skills", "mcps"):
        for item in task.get(tool_type, []) or []:
            if not isinstance(item, dict):
                continue
            execution = item.get("execution") or {}
            if not isinstance(execution, dict):
                execution = {}
            is_incorrect = item.get("is_correct") in (0, "0", False)
            failed = str(execution.get("status") or "").strip().lower() == "failure"
            label = str(execution.get("failure_category") or "").strip().upper()
            if (is_incorrect or failed) and not label:
                label = f"{tool_type[:-1].upper()}_ASSESSMENT_FAILURE"
            if label and label not in _SUCCESS_LABELS and label not in labels:
                labels.append(label)
    return labels


def ocsa_task_completed(task: dict[str, Any]) -> bool:
    value = task.get("is_complete")
    return value in (1, "1", True, "true", "True", "completed", "COMPLETED")


def ocsa_case_type(task: dict[str, Any]) -> str:
    """Derive good/bad only from OCSA's own completion and error evidence."""

    return "good" if ocsa_task_completed(task) and not ocsa_error_labels(task) else "bad"


def ocsa_primary_label(task: dict[str, Any]) -> str:
    labels = ocsa_error_labels(task)
    return labels[0] if labels else ocsa_task_failure_label(task)


def ocsa_analysis_failed(task: dict[str, Any]) -> bool:
    if ocsa_task_failure_label(task) != "UNKNOWN":
        return False
    if task.get("judge_error") or task.get("judge_error_class"):
        return True
    reasoning = str(task.get("reasoning") or "").strip().lower()
    return reasoning in {
        "assessment failed",
        "ocsa session_report returned no task result",
        "llm-as-judge returned no task result",
    } or reasoning.startswith("assessment failed:")


def ocsa_task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    """Return traceable OCSA facts for downstream JSON artifacts."""

    return {
        "task_index": task.get("task_index"),
        "message_range": task.get("message_range"),
        "is_complete": task.get("is_complete"),
        "task_failure_class": ocsa_task_failure_label(task),
        "error_labels": ocsa_error_labels(task),
        "human_intervention_level": task.get("human_intervention_level") or "unknown",
        "human_intervention_reasoning": task.get("human_intervention_reasoning") or "",
        "skills": task.get("skills") or [],
        "mcps": task.get("mcps") or [],
        "reasoning": task.get("reasoning") or "",
    }
