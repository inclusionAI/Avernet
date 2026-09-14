from __future__ import annotations

import re
from pathlib import Path

from ..constants import DIAGNOSE_RUN_SUBDIR, EVOLVE_RESULTS_BASE_DIR


def validate_id(value: str, flag: str) -> str:
    item_id = str(value or "").strip()
    if not item_id:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", item_id) or ".." in item_id:
        raise ValueError(f"Invalid {flag}: only letters, digits, underscore, dash and dot are allowed; '..' is forbidden.")
    return item_id


def validate_task_id(value: str) -> str:
    return validate_id(value, "--task-id")


def validate_step_id(value: str) -> str:
    return validate_id(value, "--step-id")


def require_task_id(value: str) -> str:
    task_id = validate_task_id(value)
    if not task_id:
        raise ValueError("Missing required --task-id: every diagnose execution must be associated with an explicit task id.")
    return task_id


def require_step_id(value: str) -> str:
    step_id = validate_step_id(value)
    if not step_id:
        raise ValueError("Missing required --step-id: every diagnose execution must report to an explicit ClawWeb step id.")
    return step_id


def resolve_output_dir(output_dir: str, task_id: str) -> Path:
    explicit_output = str(output_dir or "").strip()
    if explicit_output:
        return Path(explicit_output)
    safe_task_id = require_task_id(task_id)
    return Path(EVOLVE_RESULTS_BASE_DIR) / safe_task_id / DIAGNOSE_RUN_SUBDIR / "output"
