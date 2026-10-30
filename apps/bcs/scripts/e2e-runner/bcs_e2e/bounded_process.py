"""Bounded pipe capture for explicitly selected local command adapters.

A missing environment/cwd deliberately inherits the caller's configuration
(e.g. an approved MCP client). Product CLI callers supply their private context.
Only the child created here is reaped; saved PIDs never confer authority.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
import selectors
import subprocess
import sys
import time

from .errors import primary_error


class ProcessCaptureError(RuntimeError):
    pass


class ProcessCaptureTimeout(ProcessCaptureError):
    """Finite timeout classification; no command/output is attached."""


def capture(command: list[str], *, timeout_seconds: float, max_output_bytes: int,
            environment: dict[str, str] | None = None,
            cwd: Path | None = None,
            collect_stderr: bool = False) -> subprocess.CompletedProcess[bytes]:
    if (not isinstance(command, list) or not command
            or any(not isinstance(part, str) or not part or '\0' in part for part in command)):
        raise ProcessCaptureError('command arguments are invalid')
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120):
        raise ProcessCaptureError('process deadline is invalid')
    if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 8 * 1024 * 1024:
        raise ProcessCaptureError('process output budget is invalid')
    if type(collect_stderr) is not bool:
        raise ProcessCaptureError('stderr selection is invalid')
    child = subprocess.Popen(command, cwd=cwd, env=environment, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, umask=0o077)
    output, error_output, total = bytearray(), bytearray(), 0
    deadline = time.monotonic() + timeout_seconds
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in (child.stdout, child.stderr):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            while selector.get_map() or child.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProcessCaptureTimeout('process exceeded its execution deadline')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > max_output_bytes:
                        raise ProcessCaptureError('process output exceeded its budget')
                    if key.fileobj is child.stdout:
                        output.extend(chunk)
                    elif collect_stderr:
                        error_output.extend(chunk)
            return subprocess.CompletedProcess(
                command, child.returncode, bytes(output), bytes(error_output))
    finally:
        original = sys.exc_info()[1]
        unconfirmed = False
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            unconfirmed = True
        finally:
            for pipe in (child.stdout, child.stderr):
                try:
                    pipe.close()
                except OSError:
                    unconfirmed = True
        if unconfirmed:
            if original is None:
                original = ProcessCaptureError('process child shutdown is unconfirmed')
                original.child_shutdown_status = 'UNCONFIRMED'
                raise original
            # Do not replace the original deadline/budget failure or cancellation.
            # Adapters project only this finite secondary state, never raw errors.
            original.child_shutdown_status = 'UNCONFIRMED'
            primary_error(original).child_shutdown_status = 'UNCONFIRMED'


def scrub_failure_text(text: str, secrets) -> str:
    """Scrub credential bytes and bearer patterns from bounded diagnostics."""
    for secret in secrets:
        if secret:
            text = text.replace(secret.decode("utf-8", "replace"), "[REDACTED]")
    return re.sub(r"(?i)bearer\s+[^\s]+", "Bearer [REDACTED]", text)


def stderr_failure_detail(result, secrets=(), limit: int = 400) -> str:
    """Bounded single-line credential-scrubbed stderr tail for nonzero exits."""
    raw = getattr(result, "stderr", None) or b""
    text = scrub_failure_text(raw.decode("utf-8", "replace"), secrets)
    text = " ".join(text.split())
    return text[-limit:]
