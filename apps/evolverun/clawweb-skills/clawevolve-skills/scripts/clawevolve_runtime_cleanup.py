#!/usr/bin/env python3
"""Run one user-requested, marker-only ClawEvolve runtime cleanup."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DEFAULT_CLAWWEB_URL = "http://127.0.0.1:5173"
OPENCLAW_HOME = Path("/home/admin/.openclaw")
SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class CleanupGuardError(RuntimeError):
    """Fail closed when cleanup cannot reliably establish an idle runtime."""


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _load_cleaner():
    path = Path(__file__).with_name("cleanup_clawevolve_openclaw_runtime.py")
    spec = importlib.util.spec_from_file_location("clawevolve_runtime_cleaner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cleanup helper is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _environment_lock(openclaw_home: Path):
    lock_path = openclaw_home / "workspace" / "clawevolve_results" / ".environment.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _active_evolve_runners(
    results_root: Path,
    *,
    current_task_id: str,
    current_step_id: str,
    current_pid: int,
    proc_root: Path = Path("/proc"),
) -> list[dict[str, Any]]:
    try:
        if not results_root.exists():
            return []
        pid_files = sorted(results_root.glob("*/runner_state/*/pid"))
    except OSError as exc:
        raise CleanupGuardError(f"failed to scan runner state: {exc}") from exc

    active: list[dict[str, Any]] = []
    for pid_file in pid_files:
        task_id = pid_file.parents[2].name
        step_id = pid_file.parent.name
        try:
            raw_pid = pid_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CleanupGuardError(f"failed to read runner pid file {pid_file}: {exc}") from exc
        if not re.fullmatch(r"[1-9][0-9]*", raw_pid):
            continue
        pid = int(raw_pid)
        if task_id == current_task_id and step_id == current_step_id and pid == current_pid:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise CleanupGuardError(f"cannot inspect runner pid {pid}: {exc}") from exc
        except OSError as exc:
            raise CleanupGuardError(f"failed to inspect runner pid {pid}: {exc}") from exc
        try:
            cmdline = (proc_root / str(pid) / "cmdline").read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CleanupGuardError(f"failed to read runner cmdline for pid {pid}: {exc}") from exc
        if step_id.encode("utf-8") not in cmdline:
            continue
        active.append({"taskId": task_id, "stepId": step_id, "pid": pid})
    return active


def _report(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    url = (
        f"{args.clawweb_url.rstrip('/')}/api/evolve/internal/tasks/"
        f"{urllib.parse.quote(args.task_id)}/steps/{urllib.parse.quote(args.step_id)}/report"
    )
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60):
                return
        except Exception as exc:  # noqa: BLE001 - terminal report retries are bounded.
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"cleanup result report failed: {last_error}")


def _public_result(raw: dict[str, Any], force_cleanup: bool) -> dict[str, Any]:
    failures = raw.get("failures") if isinstance(raw.get("failures"), list) else []
    return {
        "status": "degraded" if failures or raw.get("status") == "degraded" else "ok",
        "forceCleanup": force_cleanup,
        "listedAgentCount": int(raw.get("listed") or 0),
        "listedSessionCount": int(raw.get("listed_session_count") or 0),
        "candidateAgentCount": int(raw.get("candidate_agent_count") or 0),
        "candidateSessionCount": int(raw.get("candidate_session_count") or 0),
        "deletedAgentCount": int(raw.get("deleted_agent_count") or 0),
        "deletedSessionCount": int(raw.get("deleted_session_count") or 0),
        "skippedCurrentSessionCount": int(raw.get("skipped_current_session_count") or 0),
        "failureCount": len(failures),
        "failures": failures,
    }


def _failure_result(
    code: str,
    message: str,
    *,
    force_cleanup: bool,
    active_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message[-2000:],
        "retryable": True,
    }
    if active_tasks:
        error["activeTasks"] = active_tasks
    return {
        "status": "failed",
        "forceCleanup": force_cleanup,
        "error": error,
        "deletedAgentCount": 0,
        "deletedSessionCount": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--clawweb-url", default=os.environ.get("CLAWWEB_URL") or DEFAULT_CLAWWEB_URL)
    parser.add_argument("--force-cleanup", action="store_true")
    parser.add_argument("--version", choices=("openversion", "internalversion"), default="internalversion")
    parser.add_argument("--openclaw-home")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    open_version = args.version == "openversion"
    if open_version:
        if not args.openclaw_home or not args.workspace or not args.force_cleanup:
            parser.error("openversion requires explicit Bot paths and --force-cleanup confirmation")
        openclaw_home = Path(args.openclaw_home).expanduser().resolve(strict=True)
        workspace = Path(args.workspace).expanduser().resolve(strict=True)
        # COSEC: local adapter passes the selected Bot; reject mismatched or non-directory runtime paths.
        if not openclaw_home.is_dir() or not workspace.is_dir() or not (openclaw_home / "openclaw.json").is_file():
            parser.error("invalid local Bot runtime")
        configured = json.loads((openclaw_home / "openclaw.json").read_text())
        expected = (configured.get("agents") or {}).get("defaults", {}).get("workspace")
        if not isinstance(expected, str) or Path(expected).expanduser().resolve(strict=True) != workspace:
            parser.error("workspace does not match the selected Bot configuration")
    else:
        if args.openclaw_home or args.workspace:
            parser.error("local path overrides require explicit openversion")
        openclaw_home = OPENCLAW_HOME
        workspace = openclaw_home / "workspace"
    if not SAFE_ID.fullmatch(args.task_id) or not SAFE_ID.fullmatch(args.step_id):
        parser.error("invalid task-id or step-id")

    output_dir = workspace / "clawevolve_results" / args.task_id / "cleanup" / "output"
    result_path = output_dir / "cleanup_result.json"
    failure_path = output_dir / "failure.json"
    terminal: dict[str, Any]
    report_payload: dict[str, Any]
    exit_code = 0
    try:
        with _environment_lock(openclaw_home):
            try:
                # Openversion is explicitly manual: no Runner /proc scan and no Gateway restart.
                # Internalversion retains the existing activity guards and launcher behavior.
                active_tasks = [] if open_version else _active_evolve_runners(
                    openclaw_home / "workspace" / "clawevolve_results",
                    current_task_id=args.task_id,
                    current_step_id=args.step_id,
                    current_pid=os.getpid(),
                )
                if active_tasks:
                    terminal = _failure_result(
                        "ACTIVE_EVOLVE_TASK_EXISTS",
                        "Bot 上存在运行中的进化任务，无法执行清理",
                        force_cleanup=args.force_cleanup,
                        active_tasks=active_tasks,
                    )
                    _write_json(failure_path, terminal)
                    report_payload = {
                        "status": "failed",
                        "summary": "Bot 上存在运行中的进化任务，无法执行清理",
                        "error": terminal["error"],
                    }
                    exit_code = 1
                else:
                    cleaner = _load_cleaner()
                    raw = cleaner.cleanup_runtime(
                        openclaw_path="openclaw",
                        openclaw_home=openclaw_home,
                        strict_markers=True,
                        skip_active_check=open_version,
                        excluded_session_markers=(args.task_id, args.step_id),
                    )
                    terminal = _public_result(raw, args.force_cleanup)
                    if open_version:
                        terminal.update(version="openversion", gatewayRestart=False, activeTaskCheck=False)
                    _write_json(result_path, terminal)
                    if terminal["status"] == "degraded":
                        report_payload = {
                            "status": "failed",
                            "summary": "进化运行环境清理未完全完成",
                            "error": {
                                "code": "RUNTIME_CLEANUP_PARTIAL_FAILURE",
                                "message": "部分进化 Agent 或 Session 未能删除，请查看清理结果后重试",
                                "retryable": True,
                            },
                            "output": terminal,
                        }
                        exit_code = 1
                    else:
                        report_payload = {
                            "status": "succeeded",
                            "summary": "进化运行环境清理完成",
                            "output": terminal,
                        }
            except CleanupGuardError as exc:
                terminal = _failure_result(
                    "RUNTIME_CLEANUP_GUARD_FAILED",
                    str(exc),
                    force_cleanup=args.force_cleanup,
                )
                _write_json(failure_path, terminal)
                report_payload = {
                    "status": "failed",
                    "summary": "进化运行环境安全检查失败",
                    "error": terminal["error"],
                }
                exit_code = 1
            except BaseException as exc:
                terminal = _failure_result(
                    "RUNTIME_CLEANUP_FAILED",
                    str(exc),
                    force_cleanup=args.force_cleanup,
                )
                _write_json(failure_path, terminal)
                report_payload = {
                    "status": "failed",
                    "summary": "进化运行环境清理失败",
                    "error": terminal["error"],
                }
                exit_code = 1
    except CleanupGuardError as exc:
        terminal = _failure_result(
            "RUNTIME_CLEANUP_GUARD_FAILED",
            str(exc),
            force_cleanup=args.force_cleanup,
        )
        _write_json(failure_path, terminal)
        report_payload = {
            "status": "failed",
            "summary": "进化运行环境安全检查失败",
            "error": terminal["error"],
        }
        exit_code = 1
    except BaseException as exc:
        terminal = _failure_result(
            "RUNTIME_CLEANUP_GUARD_FAILED",
            str(exc),
            force_cleanup=args.force_cleanup,
        )
        _write_json(failure_path, terminal)
        report_payload = {
            "status": "failed",
            "summary": "进化运行环境安全检查失败",
            "error": terminal["error"],
        }
        exit_code = 1

    try:
        _report(args, report_payload)
    except Exception as report_error:  # noqa: BLE001
        print(f"failed to report cleanup result: {report_error}", file=__import__("sys").stderr)
        return 1
    print(json.dumps(terminal, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
