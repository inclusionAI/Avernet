from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import logger
from ..io import atomic_write_text


class DiscoveryAgentError(RuntimeError):
    """Raised when the discovery agent transport cannot be started."""


@dataclass(frozen=True)
class DiscoveryAgentRunResult:
    status: str
    agent_id: str
    session_id: str
    response_text: str
    elapsed_seconds: float
    stdout_text: str = ""
    stderr_text: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    agent_json: Any = None
    transport: str = "cli"

    @property
    def returncode(self) -> int:
        """Compatibility accessor for older call sites/tests."""

        if self.status in {"success", "succeeded", "completed", "done", "ok", "artifact"}:
            return 0
        return int(self.diagnostics.get("exitCode", 1) or 1)

    @property
    def stdout(self) -> str:
        """Compatibility accessor for older call sites/tests."""

        return self.stdout_text or self.response_text

    @property
    def stderr(self) -> str:
        """Compatibility accessor for older call sites/tests."""

        return self.stderr_text


# Keep discovery bounded. These defaults intentionally mirror workflow's CLI
# approach but allow operators to tune plan separately without code changes.
_AGENT_LIST_TIMEOUT_SECONDS = int(os.environ.get("CLAWEVOLVE_PLAN_AGENT_LIST_TIMEOUT", "30"))
_AGENT_ADD_TIMEOUT_SECONDS = int(os.environ.get("CLAWEVOLVE_PLAN_AGENT_ADD_TIMEOUT", "120"))
_AGENT_DELETE_TIMEOUT_SECONDS = int(os.environ.get("CLAWEVOLVE_PLAN_AGENT_DELETE_TIMEOUT", "60"))
_REGISTRATION_LOCK_TIMEOUT_SECONDS = int(os.environ.get("CLAWEVOLVE_PLAN_AGENT_LOCK_TIMEOUT", "60"))
_GATEWAY_VISIBILITY_DELAYS_SECONDS = (0.5, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0)
_DEFAULT_MODEL = os.environ.get("CLAWEVOLVE_PLAN_DISCOVERY_MODEL", "openai/gpt-4.1-mini")
_DEFAULT_AGENT_NAME = "clawevolve-plan-discovery"


def run_openclaw_agent_message(
    *,
    message: str,
    workspace_root: Path,
    task_id: str,
    timeout_seconds: int = 900,
    output_path: Path | None = None,
) -> DiscoveryAgentRunResult:
    """Run an isolated, task-scoped OpenClaw agent during the Plan process.

    The agent is registered at runtime. Embedded local execution is preferred;
    Gateway execution is only a credential/runtime fallback. A per-agent lock
    prevents concurrent runs from deleting a shared task-scoped registration.
    """

    agent_id = os.environ.get("CLAWEVOLVE_PLAN_DISCOVERY_AGENT_ID") or _task_scoped_agent_id(task_id)
    session_id = f"{agent_id}_{int(time.time() * 1000)}"
    with _agent_lifecycle_lock(agent_id):
        return _run_cli_agent_message(
            agent_id=agent_id,
            session_id=session_id,
            message=message,
            workspace=workspace_root,
            timeout_seconds=timeout_seconds,
            output_path=output_path,
        )


def _run_cli_agent_message(
    *,
    agent_id: str,
    session_id: str,
    message: str,
    workspace: Path,
    timeout_seconds: int,
    output_path: Path | None,
) -> DiscoveryAgentRunResult:
    """Register and run a temporary OpenClaw agent safely.

    Registration is serialized because ``agents add/delete`` mutate the shared
    OpenClaw config. Execution prefers embedded local mode, which reads the
    freshly written registry directly and avoids Gateway registry propagation.
    Gateway execution is used only when local execution cannot access provider
    credentials/runtime, and only after the Gateway explicitly lists the agent.
    """

    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    openclaw_path = os.environ.get("OPENCLAW_PATH", "openclaw")
    model = os.environ.get("CLAWEVOLVE_PLAN_DISCOVERY_MODEL", _DEFAULT_MODEL)
    env = _openclaw_env()
    diagnostics: dict[str, Any] = {
        "transport": "openclaw-cli",
        "openclawPath": openclaw_path,
        "model": model,
        "workspace": str(workspace),
        "outputPath": str(output_path or ""),
    }

    logger.info(
        "discovery dynamic agent start",
        agent_id=agent_id,
        session_id=session_id,
        workspace=str(workspace),
        prompt_chars=len(message or ""),
        timeout_seconds=timeout_seconds,
    )

    bootstrap_before_add = _snapshot_workspace_bootstrap(workspace)
    created = False
    started_at = time.time()
    proc: dict[str, Any] = _empty_command_result()
    selected_transport = "local"

    try:
        try:
            with _registration_lock():
                _validate_openclaw_config(openclaw_path, env, diagnostics)
                exists = _agent_in_json_registry(
                    openclaw_path, agent_id, env, diagnostics, phase="beforeAdd"
                )
                if exists:
                    diagnostics["agentRegistration"] = "already-exists"
                else:
                    add_cmd = [
                        openclaw_path,
                        "agents",
                        "add",
                        agent_id,
                        "--model",
                        model,
                        "--workspace",
                        str(workspace),
                        "--non-interactive",
                        "--json",
                    ]
                    diagnostics["addCmd"] = _redacted_cmd(add_cmd)
                    add_proc = _run_command(
                        add_cmd,
                        timeout=_AGENT_ADD_TIMEOUT_SECONDS,
                        env=env,
                        cwd=None,
                    )
                    _record_command(diagnostics, "add", add_proc)

                    listed_after_add = _agent_in_json_registry(
                        openclaw_path, agent_id, env, diagnostics, phase="afterAdd"
                    )
                    if not listed_after_add:
                        reason = _command_failure_reason(add_proc)
                        raise DiscoveryAgentError(
                            f"Agent '{agent_id}' registration failed ({reason}); "
                            f"it is absent from the JSON registry. "
                            f"diagnostics={_preview(diagnostics, 4000)}"
                        )
                    created = True
                    diagnostics["agentRegistration"] = (
                        "created-ok"
                        if add_proc.get("returncode") == 0 and not add_proc.get("timed_out")
                        else "created-tolerant(add-failed-but-listed)"
                    )
        except Exception:
            cleaned = _cleanup_workspace_bootstrap(
                workspace, bootstrap_before_add, restore_existing=True
            )
            if cleaned:
                diagnostics["workspaceBootstrapCleanedAfterAdd"] = cleaned
            raise

        cleaned = _cleanup_workspace_bootstrap(
            workspace, bootstrap_before_add, restore_existing=True
        )
        if cleaned:
            diagnostics["workspaceBootstrapCleanedAfterAdd"] = cleaned

        bootstrap_before_agent = _snapshot_workspace_bootstrap(workspace)
        local_timeout = _require_remaining_timeout(started_at, timeout_seconds)
        local_cmd = _agent_command(
            openclaw_path=openclaw_path,
            agent_id=agent_id,
            session_id=session_id,
            message=message,
            local=True,
            timeout_seconds=local_timeout,
        )
        diagnostics["localAgentCmd"] = _display_agent_command(local_cmd, message)
        logger.info(
            "discovery local agent message start",
            agent_id=agent_id,
            session_id=session_id,
        )
        proc = _run_command(
            local_cmd,
            timeout=local_timeout,
            env=env,
            cwd=workspace,
        )
        _record_command(diagnostics, "localAgent", proc, output_limit=3000)

        if _command_succeeded(proc):
            diagnostics["agentExecution"] = "local-success"
        elif _should_fallback_to_gateway(proc):
            selected_transport = "gateway"
            diagnostics["localFallbackReason"] = _command_failure_reason(proc)
            visible = _wait_gateway_agent_visible(
                openclaw_path=openclaw_path,
                agent_id=agent_id,
                env=env,
                diagnostics=diagnostics,
                started_at=started_at,
                timeout_seconds=timeout_seconds,
            )
            if not visible:
                diagnostics["failureCode"] = "gateway_agent_registry_not_synced"
                proc = {
                    "returncode": 1,
                    "stdout": str(proc.get("stdout") or ""),
                    "stderr": (
                        "Gateway runtime did not expose dynamically registered agent "
                        f"'{agent_id}' before the visibility deadline."
                    ),
                    "timed_out": False,
                    "elapsed": time.time() - started_at,
                }
            else:
                gateway_timeout = _require_remaining_timeout(
                    started_at, timeout_seconds
                )
                gateway_cmd = _agent_command(
                    openclaw_path=openclaw_path,
                    agent_id=agent_id,
                    session_id=session_id,
                    message=message,
                    local=False,
                    timeout_seconds=gateway_timeout,
                )
                diagnostics["gatewayAgentCmd"] = _display_agent_command(
                    gateway_cmd, message
                )
                logger.info(
                    "discovery gateway agent message start",
                    agent_id=agent_id,
                    session_id=session_id,
                )
                proc = _run_command(
                    gateway_cmd,
                    timeout=gateway_timeout,
                    env=env,
                    cwd=workspace,
                )
                _record_command(
                    diagnostics, "gatewayAgent", proc, output_limit=3000
                )
                diagnostics["agentExecution"] = (
                    "gateway-success" if _command_succeeded(proc) else "gateway-failed"
                )
        else:
            diagnostics["agentExecution"] = "local-failed-no-fallback"

        changed = _detect_workspace_bootstrap_changes(
            workspace, bootstrap_before_agent
        )
        if changed:
            diagnostics["workspaceBootstrapChangedAfterRun"] = changed
    finally:
        if created:
            try:
                with _registration_lock():
                    _cleanup_registered_agent(
                        openclaw_path, agent_id, env, diagnostics
                    )
            except Exception as exc:  # Cleanup must not hide the primary result.
                diagnostics["agentCleanup"] = "failed"
                diagnostics["agentCleanupError"] = str(exc)
                logger.warning(
                    "temporary discovery agent cleanup failed",
                    agent_id=agent_id,
                    error=str(exc),
                )

    return _build_agent_result(
        proc=proc,
        agent_id=agent_id,
        session_id=session_id,
        started_at=started_at,
        output_path=output_path,
        diagnostics=diagnostics,
        transport=selected_transport,
    )


def _validate_openclaw_config(
    openclaw_path: str,
    env: dict[str, str],
    diagnostics: dict[str, Any],
) -> None:
    proc = _run_command(
        [openclaw_path, "config", "validate", "--json"],
        timeout=_AGENT_LIST_TIMEOUT_SECONDS,
        env=env,
        cwd=None,
    )
    _record_command(diagnostics, "configValidate", proc)
    if not _command_succeeded(proc):
        diagnostics["failureCode"] = "openclaw_config_invalid"
        raise DiscoveryAgentError(
            "OpenClaw config validation failed; dynamic agent registration was not "
            f"attempted. diagnostics={_preview(diagnostics, 4000)}"
        )


def _agent_in_json_registry(
    openclaw_path: str,
    agent_id: str,
    env: dict[str, str],
    diagnostics: dict[str, Any],
    *,
    phase: str,
) -> bool:
    proc = _run_command(
        [openclaw_path, "agents", "list", "--json"],
        timeout=_AGENT_LIST_TIMEOUT_SECONDS,
        env=env,
        cwd=None,
    )
    _record_command(diagnostics, f"agentsList{phase}", proc)
    if not _command_succeeded(proc):
        diagnostics["failureCode"] = "openclaw_agent_registry_unavailable"
        raise DiscoveryAgentError(
            "Unable to read OpenClaw agent registry as JSON; refusing to infer that "
            f"the agent is absent. diagnostics={_preview(diagnostics, 4000)}"
        )
    try:
        payload = json.loads(str(proc.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        diagnostics["failureCode"] = "openclaw_agent_registry_invalid_json"
        raise DiscoveryAgentError(
            f"OpenClaw agents list returned invalid JSON: {exc}. "
            f"diagnostics={_preview(diagnostics, 4000)}"
        ) from exc
    names = _extract_agent_ids(payload)
    diagnostics[f"agentsList{phase}Ids"] = sorted(names)
    normalized = _normalize_agent_id(agent_id)
    return agent_id.lower() in names or normalized in names


def _agent_command(
    *,
    openclaw_path: str,
    agent_id: str,
    session_id: str,
    message: str,
    local: bool,
    timeout_seconds: int,
) -> list[str]:
    cmd = [openclaw_path, "agent"]
    if local:
        cmd.append("--local")
    cmd.extend(
        [
            "--agent",
            agent_id,
            "--session-id",
            session_id,
            "--message",
            message,
            "--timeout",
            str(timeout_seconds),
            "--json",
        ]
    )
    return cmd


def _display_agent_command(cmd: list[str], message: str) -> str:
    display = list(cmd)
    try:
        index = display.index("--message") + 1
        display[index] = f"<prompt:{len(message or '')} chars>"
    except (ValueError, IndexError):
        pass
    return _redacted_cmd(display)


def _wait_gateway_agent_visible(
    *,
    openclaw_path: str,
    agent_id: str,
    env: dict[str, str],
    diagnostics: dict[str, Any],
    started_at: float,
    timeout_seconds: int,
) -> bool:
    attempts: list[dict[str, Any]] = []
    for attempt, delay in enumerate((0.0, *_GATEWAY_VISIBILITY_DELAYS_SECONDS), 1):
        if delay:
            if _remaining_timeout(started_at, timeout_seconds) <= delay:
                break
            time.sleep(delay)
        proc = _run_command(
            [
                openclaw_path,
                "gateway",
                "call",
                "agents.list",
                "--params",
                "{}",
                "--timeout",
                "5000",
                "--json",
            ],
            timeout=min(
                10, _require_remaining_timeout(started_at, timeout_seconds)
            ),
            env=env,
            cwd=None,
        )
        entry = {
            "attempt": attempt,
            "delaySeconds": delay,
            "exitCode": proc.get("returncode"),
            "timedOut": bool(proc.get("timed_out")),
            "stderr": _tail(proc.get("stderr", ""), 1000),
        }
        if _command_succeeded(proc):
            try:
                ids = _extract_agent_ids(json.loads(str(proc.get("stdout") or "")))
                entry["agentIds"] = sorted(ids)
                attempts.append(entry)
                normalized = _normalize_agent_id(agent_id)
                if agent_id.lower() in ids or normalized in ids:
                    diagnostics["gatewayVisibilityAttempts"] = attempts
                    diagnostics["gatewayAgentVisible"] = True
                    return True
                continue
            except json.JSONDecodeError as exc:
                entry["parseError"] = str(exc)
        attempts.append(entry)
    diagnostics["gatewayVisibilityAttempts"] = attempts
    diagnostics["gatewayAgentVisible"] = False
    return False


def _cleanup_registered_agent(
    openclaw_path: str,
    agent_id: str,
    env: dict[str, str],
    diagnostics: dict[str, Any],
) -> None:
    cmd = [
        openclaw_path,
        "agents",
        "delete",
        agent_id,
        "--force",
        "--json",
    ]
    diagnostics["deleteCmd"] = _redacted_cmd(cmd)
    proc = _run_command(
        cmd,
        timeout=_AGENT_DELETE_TIMEOUT_SECONDS,
        env=env,
        cwd=None,
    )
    _record_command(diagnostics, "delete", proc)
    diagnostics["agentCleanup"] = (
        "deleted" if _command_succeeded(proc) else "delete-failed"
    )


def _should_fallback_to_gateway(proc: dict[str, Any]) -> bool:
    if proc.get("timed_out"):
        return False
    text = (
        str(proc.get("stdout") or "") + "\n" + str(proc.get("stderr") or "")
    ).lower()
    indicators = (
        "api key",
        "api-key",
        "authentication",
        "unauthorized",
        "credential",
        "auth profile",
        "provider auth",
        "provider configuration",
        "provider not configured",
        "missing provider",
        "no provider",
    )
    return any(indicator in text for indicator in indicators)


def _extract_agent_ids(payload: Any) -> set[str]:
    ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"id", "agentid", "agent_id", "name"} and isinstance(item, str):
                    candidate = item.strip().lower()
                    if candidate:
                        ids.add(candidate)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return ids


def _record_command(
    diagnostics: dict[str, Any],
    prefix: str,
    proc: dict[str, Any],
    *,
    output_limit: int = 3000,
) -> None:
    diagnostics[f"{prefix}ExitCode"] = proc.get("returncode")
    diagnostics[f"{prefix}TimedOut"] = bool(proc.get("timed_out"))
    diagnostics[f"{prefix}ElapsedSeconds"] = proc.get("elapsed")
    diagnostics[f"{prefix}Stdout"] = _tail(proc.get("stdout", ""), output_limit)
    diagnostics[f"{prefix}Stderr"] = _tail(proc.get("stderr", ""), output_limit)


def _command_succeeded(proc: dict[str, Any]) -> bool:
    return proc.get("returncode") == 0 and not proc.get("timed_out")


def _command_failure_reason(proc: dict[str, Any]) -> str:
    if proc.get("timed_out"):
        return "timeout"
    stderr = _tail(proc.get("stderr", ""), 500).strip()
    stdout = _tail(proc.get("stdout", ""), 500).strip()
    return stderr or stdout or f"exit_code={proc.get('returncode')}"


def _remaining_timeout(started_at: float, timeout_seconds: int) -> int:
    return max(0, int(timeout_seconds - (time.time() - started_at)))


def _require_remaining_timeout(started_at: float, timeout_seconds: int) -> int:
    remaining = _remaining_timeout(started_at, timeout_seconds)
    if remaining <= 0:
        raise DiscoveryAgentError(
            "OpenClaw agent execution deadline was exhausted before the next command"
        )
    return remaining


def _empty_command_result() -> dict[str, Any]:
    return {
        "returncode": 1,
        "stdout": "",
        "stderr": "agent execution did not start",
        "timed_out": False,
        "elapsed": 0.0,
    }


def _build_agent_result(
    *,
    proc: dict[str, Any],
    agent_id: str,
    session_id: str,
    started_at: float,
    output_path: Path | None,
    diagnostics: dict[str, Any],
    transport: str,
) -> DiscoveryAgentRunResult:
    elapsed = time.time() - started_at
    stdout = str(proc.get("stdout") or "")
    stderr = str(proc.get("stderr") or "")
    timed_out = bool(proc.get("timed_out"))
    exit_code = int(
        proc.get("returncode", -1) if proc.get("returncode") is not None else -1
    )
    artifact_exists = bool(output_path and output_path.exists())

    agent_json = None
    response_text = ""
    if stdout.strip():
        try:
            agent_json = json.loads(stdout)
            response_text = _json_response_text(agent_json)
        except json.JSONDecodeError:
            response_text = stdout.strip()

    status = "timeout" if timed_out else ("success" if exit_code == 0 else "failed")
    diagnostics.update(
        {
            "exitCode": exit_code,
            "timedOut": timed_out,
            "artifactExists": artifact_exists,
            "stdoutPreview": _tail(stdout, 3000),
            "stderrPreview": _tail(stderr, 3000),
            "selectedTransport": transport,
            "responseExtracted": bool(response_text),
            "responseEnvelopeKeys": (
                sorted(agent_json.keys()) if isinstance(agent_json, dict) else []
            ),
        }
    )
    logger.info(
        "discovery dynamic agent done",
        agent_id=agent_id,
        session_id=session_id,
        status=status,
        transport=transport,
        exit_code=exit_code,
        timed_out=timed_out,
        elapsed_seconds=f"{elapsed:.2f}",
        artifact_exists=artifact_exists,
        cleanup=diagnostics.get("agentCleanup", "not-owned"),
    )
    return DiscoveryAgentRunResult(
        status=status,
        agent_id=agent_id,
        session_id=session_id,
        response_text=response_text,
        elapsed_seconds=elapsed,
        stdout_text=stdout,
        stderr_text=stderr,
        diagnostics=diagnostics,
        agent_json=agent_json,
        transport=transport,
    )


@contextmanager
def _agent_lifecycle_lock(agent_id: str):
    safe_agent_id = _safe_id(agent_id)
    lock_path = Path(
        os.environ.get(
            "CLAWEVOLVE_PLAN_AGENT_LIFECYCLE_LOCK_DIR",
            "/tmp/clawevolve-openclaw-agent-locks",
        )
    ) / f"{safe_agent_id}.lock"
    with _file_lock(lock_path, timeout_seconds=_REGISTRATION_LOCK_TIMEOUT_SECONDS):
        yield


@contextmanager
def _registration_lock():
    lock_path = Path(
        os.environ.get(
            "CLAWEVOLVE_PLAN_AGENT_REGISTRATION_LOCK",
            "/tmp/clawevolve-openclaw-agent-registration.lock",
        )
    )
    with _file_lock(lock_path, timeout_seconds=_REGISTRATION_LOCK_TIMEOUT_SECONDS):
        yield


@contextmanager
def _file_lock(lock_path: Path, *, timeout_seconds: int):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DiscoveryAgentError(
                        f"Timed out waiting for OpenClaw lock: {lock_path}"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_OPENCLAW_BOOTSTRAP_FILES = (
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "TOOLS.md",
    "USER.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
)


def prepare_plan_workspace(workspace: Path) -> None:
    """Initialize only Plan's dedicated contract/document workspace, never the Bot workspace.

    Seed neutral context before registration is snapshotted, so restoration after
    agents add preserves valid initialization instead of deleting every new file.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    templates = Path(__file__).with_name("workspace_defaults")
    defaults = {name: (templates / name).read_text(encoding="utf-8") for name in _OPENCLAW_BOOTSTRAP_FILES}
    for name in defaults:
        target = workspace / name
        # COSEC: never follow context symlinks into the Bot's main workspace.
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise DiscoveryAgentError(f"Unsafe contract workspace context path: {target}")
    for name, content in defaults.items():
        atomic_write_text(workspace / name, content)


def _snapshot_workspace_bootstrap(workspace: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for name in (*_OPENCLAW_BOOTSTRAP_FILES, ".git"):
        path = workspace / name
        item: dict[str, Any] = {
            "exists": path.exists(),
            "is_dir": path.is_dir(),
            "content": None,
        }
        try:
            if path.is_file():
                item["content"] = path.read_bytes()
        except Exception:
            pass
        snapshot[name] = item
    return snapshot


def _cleanup_workspace_bootstrap(
    workspace: Path,
    before: dict[str, dict[str, Any]],
    *,
    restore_existing: bool = False,
) -> list[str]:
    cleaned: list[str] = []
    for name in (*_OPENCLAW_BOOTSTRAP_FILES, ".git"):
        old = before.get(name) or {"exists": False, "content": None}
        path = workspace / name
        try:
            if not old.get("exists"):
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    cleaned.append(f"removed-new:{name}")
                continue
            if name == ".git":
                continue
            content = old.get("content")
            if restore_existing and content is not None and path.is_file():
                if path.read_bytes() != content:
                    path.write_bytes(content)
                    cleaned.append(f"restored-existing:{name}")
        except Exception as exc:  # Cleanup failures must be visible to callers.
            cleaned.append(f"cleanup-failed:{name}:{type(exc).__name__}:{exc}")
    return cleaned


def _detect_workspace_bootstrap_changes(
    workspace: Path, before: dict[str, dict[str, Any]]
) -> list[str]:
    changed: list[str] = []
    for name in (*_OPENCLAW_BOOTSTRAP_FILES, ".git"):
        old = before.get(name) or {"exists": False, "content": None}
        path = workspace / name
        try:
            if not old.get("exists"):
                if path.exists():
                    changed.append(f"created:{name}")
                continue
            if name == ".git":
                continue
            content = old.get("content")
            if content is not None and path.is_file() and path.read_bytes() != content:
                changed.append(f"modified:{name}")
        except Exception:
            changed.append(f"modified-or-unreadable:{name}")
    return changed


def _openclaw_env() -> dict[str, str]:
    env = os.environ.copy()
    gw_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if not gw_token:
        try:
            oc_home = os.environ.get("OPENCLAW_HOME") or os.path.expanduser("~/.openclaw")
            oc_cfg = json.loads(Path(oc_home, "openclaw.json").read_text(encoding="utf-8"))
            gw_token = str((oc_cfg.get("gateway", {}) or {}).get("auth", {}).get("token") or "")
        except Exception:
            gw_token = ""
    if gw_token:
        env["OPENCLAW_GATEWAY_TOKEN"] = gw_token
    return env


def _run_command(
    cmd: list[str],
    *,
    timeout: int | float,
    env: dict[str, str],
    cwd: Path | None,
) -> dict[str, Any]:
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd) if cwd else None,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": f"openclaw command not found: {exc}",
            "timed_out": False,
            "elapsed": time.time() - start,
        }
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "returncode": proc.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timed_out": False,
            "elapsed": time.time() - start,
        }
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        stdout = _coerce_subprocess_output(exc.stdout)
        stderr = _coerce_subprocess_output(exc.stderr)
        # Best-effort drain after kill; ignore errors because timeout is already diagnostic enough.
        try:
            more_out, more_err = proc.communicate(timeout=2)
            stdout += more_out or ""
            stderr += more_err or ""
        except Exception:
            pass
        return {
            "returncode": proc.returncode if proc.returncode is not None else -1,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
            "elapsed": time.time() - start,
        }


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = proc.pid
    for sig, wait_seconds in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            break
        try:
            proc.wait(timeout=wait_seconds)
            break
        except subprocess.TimeoutExpired:
            continue


def _coerce_subprocess_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _json_response_text(value: Any) -> str:
    """Extract assistant text from supported OpenClaw JSON envelopes."""
    if isinstance(value, dict):
        for key in (
            "response",
            "finalResponse",
            "final_response",
            "result",
            "output",
            "text",
            "message",
            "content",
        ):
            item = value.get(key)
            text = _stringify_text(item)
            if text:
                return text
            if isinstance(item, (dict, list)):
                text = _json_response_text(item)
                if text:
                    return text
        for key in ("data", "payload", "payloads"):
            text = _json_response_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        # OpenClaw payload collections are chronological. Prefer the last
        # response that contains text, which represents the final assistant
        # answer when progress/status payloads precede it.
        for item in reversed(value):
            text = _json_response_text(item)
            if text:
                return text
    if isinstance(value, str):
        return value.strip()
    return ""


def _stringify_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            text = _stringify_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        return "\n".join(filter(None, (_stringify_text(item) for item in value))).strip()
    return ""


def _normalize_agent_id(value: str) -> str:
    return str(value or "").replace(":", "-").lower()


def _task_scoped_agent_id(task_id: str) -> str:
    """Return a stable, bounded id safe for OpenClaw config and logs."""

    readable = _safe_id(task_id)[:20] or "task"
    digest = hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()[:12]
    return f"{_DEFAULT_AGENT_NAME}-{readable}-{digest}"


def _safe_id(value: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in str(value or "task"))
    return out.strip(".-_")[:48] or "task"


def _tail(value: Any, limit: int) -> str:
    text = _coerce_subprocess_output(value)
    return text[-limit:] if len(text) > limit else text


def _redacted_cmd(cmd: list[str]) -> str:
    redacted: list[str] = []
    skip_secret = False
    for part in cmd:
        if skip_secret:
            redacted.append("<redacted>")
            skip_secret = False
            continue
        redacted.append(part)
        if part in {"--api-key", "--token"}:
            skip_secret = True
    return " ".join(redacted)


def _preview(value: Any, limit: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    text = text.replace("\n", "\\n")
    return text[:limit]
