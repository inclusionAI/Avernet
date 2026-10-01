from __future__ import annotations

import re
from pathlib import Path

from ..constants import DIAGNOSE_RUN_SUBDIR, EVOLVE_RESULTS_BASE_DIR


def _results_base(evolve_results_dir: str = "") -> Path:
    explicit = str(evolve_results_dir or "").strip()
    return Path(explicit).expanduser() if explicit else Path(EVOLVE_RESULTS_BASE_DIR)


def resolve_run_dir(run_dir: str, task_id: str, evolve_results_dir: str = "") -> str:
    explicit = str(run_dir or "").strip()
    if explicit:
        return explicit
    return str(_results_base(evolve_results_dir) / validate_task_id(task_id) / DIAGNOSE_RUN_SUBDIR)


def output_dirs(task_id: str, evolve_results_dir: str = "") -> tuple[Path, Path, Path]:
    safe_task_id = validate_task_id(task_id)
    run_root_dir = _results_base(evolve_results_dir) / safe_task_id
    plan_dir = run_root_dir / "plan"
    input_dir = plan_dir / "input"
    output_dir = plan_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_root_dir, input_dir, output_dir


def validate_task_id(value: str) -> str:
    return validate_id(value, "--task-id")


def validate_step_id(value: str) -> str:
    return validate_id(value, "--step-id")


def validate_id(value: str, flag: str) -> str:
    item_id = str(value or "").strip()
    if not item_id:
        raise ValueError(f"Missing {flag}.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", item_id) or ".." in item_id:
        raise ValueError(f"Invalid {flag}: only letters, digits, underscore, dash and dot are allowed; '..' is forbidden.")
    return item_id
