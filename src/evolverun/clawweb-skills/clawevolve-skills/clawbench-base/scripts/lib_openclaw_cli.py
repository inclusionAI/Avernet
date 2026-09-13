"""Small, task-agnostic OpenClaw agent command runner for ClawBench."""

from __future__ import annotations

import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Callable, Literal, cast


OpenClawExecutionMode = Literal["local", "gateway"]

DEFAULT_EXECUTION_MODE: OpenClawExecutionMode = "local"
GATEWAY_AGENT_VISIBILITY_ATTEMPTS = 6
GATEWAY_AGENT_VISIBILITY_DELAY_SECONDS = 10.0


def resolve_openclaw_execution_mode(value: str | None = None) -> OpenClawExecutionMode:
    raw = str(
        value
        if value is not None
        else os.environ.get("CLAWBENCH_OPENCLAW_EXECUTION_MODE", DEFAULT_EXECUTION_MODE)
    ).strip().lower()
    if raw not in {"local", "gateway"}:
        raise ValueError(
            "openclaw execution mode must be 'local' or 'gateway', "
            f"got {value!r}"
        )
    return cast(OpenClawExecutionMode, raw)


def _is_unknown_agent_failure(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 0:
        return False
    text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return "unknown agent" in text or "agent not found" in text


def run_openclaw_agent(
    *,
    agent_id: str,
    session_id: str,
    message: str,
    workspace: Path,
    timeout_seconds: float,
    mode: str | None = None,
    openclaw_path: str | None = None,
    json_output: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    use_shell: bool | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one OpenClaw agent turn without changing its business payload.

    Gateway mode retries only the narrow post-registration visibility failure.
    Other failures, including timeouts and authentication errors, are returned
    unchanged to the existing ClawBench caller.
    """

    resolved_mode = resolve_openclaw_execution_mode(mode)
    shell = platform.system() == "Windows" if use_shell is None else use_shell
    send_message = (
        message.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
        if shell
        else message
    )
    command = [openclaw_path or os.environ.get("OPENCLAW_PATH", "openclaw"), "agent"]
    if resolved_mode == "local":
        command.append("--local")
    command.extend([
        "--agent", agent_id,
        "--session-id", session_id,
        "--message", send_message,
    ])
    if json_output:
        command.append("--json")

    attempts = GATEWAY_AGENT_VISIBILITY_ATTEMPTS if resolved_mode == "gateway" else 1
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        result = runner(
            command,
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=timeout_seconds,
            check=False,
            shell=shell,
        )
        if not _is_unknown_agent_failure(result) or attempt == attempts:
            return result
        sleep(GATEWAY_AGENT_VISIBILITY_DELAY_SECONDS)

    assert result is not None
    return result
