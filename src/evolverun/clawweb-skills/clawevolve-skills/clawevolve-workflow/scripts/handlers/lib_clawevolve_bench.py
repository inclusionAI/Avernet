#!/usr/bin/env python3
"""Small, task-agnostic client for the clawevolve-bench product CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class ClawEvolveBenchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "CLAWBENCH_FAILED",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _log_tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:]


def _find_workflow(workspace: Path, skill_base_dir: Path | None = None) -> Path:
    candidates = []
    configured = skill_base_dir or (
        Path(os.environ["SKILL_BASE_DIR"]).expanduser() if os.environ.get("SKILL_BASE_DIR") else None
    )
    if configured:
        base = Path(configured).expanduser().resolve()
        candidate = (base / "clawevolve-bench/scripts/clawbench-workflow.py").resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise ClawEvolveBenchError(f"clawevolve-bench workflow escapes SKILL_BASE_DIR: {candidate}") from exc
        candidates.append(candidate)
    candidates.append(Path(__file__).resolve().parents[3] / "clawevolve-bench/scripts/clawbench-workflow.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ClawEvolveBenchError("clawevolve-bench/scripts/clawbench-workflow.py not found")


def run_clawevolve_bench(
    *,
    owner_id: str,
    domain_id: str,
    work_dir: Path,
    model: str,
    suite: str = "all",
    scene: str = "claw-evolve-bench",
    judge: str = "",
    report: bool = True,
    context_dir: Path | None = None,
    timeout_seconds: int = 86400,
    workspace: Path | None = None,
    skill_base_dir: Path | None = None,
    clawweb_url: str = "",
    evolve_task_id: str = "",
    evolve_step_id: str = "",
    trace_role: str = "",
    openclaw_execution_mode: str = "local",
) -> dict[str, Any]:
    """Execute one standard ClawBench Run and return its validated result."""
    owner_id, domain_id = str(owner_id or "").strip(), str(domain_id or "").strip()
    if not owner_id or not domain_id:
        raise ClawEvolveBenchError("owner-id and domain-id are required")
    work_dir = Path(work_dir).expanduser().resolve()
    workspace = Path(workspace or os.environ.get("OPENCLAW_WORKSPACE") or "/home/admin/.openclaw/workspace").expanduser().resolve()
    workflow = _find_workflow(workspace, Path(skill_base_dir).resolve() if skill_base_dir else None)
    result_path = work_dir / "workflow_result.json"
    log_path = work_dir / "logs/clawevolve-bench.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-u", "-B", str(workflow), "run",
        "--owner-id", owner_id, "--domain-id", domain_id,
        "--work-dir", str(work_dir), "--model", str(model),
        "--suite", str(suite), "--scene", str(scene),
        "--timeout-seconds", str(timeout_seconds),
        "--openclaw-execution-mode", str(openclaw_execution_mode or "local"),
    ]
    if context_dir:
        cmd += ["--context-dir", str(Path(context_dir).expanduser().resolve())]
    if judge:
        cmd += ["--judge", str(judge)]
    if not report:
        cmd.append("--no-report")
    if clawweb_url:
        cmd += ["--clawweb-url", str(clawweb_url)]
    if evolve_task_id:
        cmd += ["--evolve-task-id", evolve_task_id]
    if evolve_step_id:
        cmd += ["--evolve-step-id", evolve_step_id]
    if trace_role:
        cmd += ["--trace-role", trace_role]
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=timeout_seconds + 900)
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        tail = _log_tail(log_path)
        raise ClawEvolveBenchError(
            f"clawevolve-bench did not produce a readable result: exit={proc.returncode}, "
            f"log={log_path}: {exc}" + (f"; log_tail={tail}" if tail else "")
        ) from exc
    if proc.returncode != 0 or str(result.get("status") or "").lower() != "succeeded":
        error = result.get("error")
        if isinstance(error, dict):
            error_code = str(error.get("code") or "CLAWBENCH_FAILED")
            error_message = str(error.get("message") or error_code)
            retryable = error.get("retryable") is not False
        else:
            error_code = "CLAWBENCH_FAILED"
            error_message = str(error or "unknown clawevolve-bench failure")
            retryable = True
        raise ClawEvolveBenchError(
            f"clawevolve-bench failed: exit={proc.returncode}, code={error_code}, "
            f"error={error_message}, log={log_path}",
            code=error_code,
            retryable=retryable,
        )
    missing = [key for key in ("benchRunId", "domainId", "metrics", "inputPath", "resultPath") if result.get(key) in (None, "")]
    if missing:
        raise ClawEvolveBenchError(f"clawevolve-bench result missing {', '.join(missing)}: {result_path}")
    return result
