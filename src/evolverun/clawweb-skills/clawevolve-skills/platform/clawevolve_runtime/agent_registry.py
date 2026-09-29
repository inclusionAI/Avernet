"""Serialize OpenClaw registry mutations across Evolve processes.

Agents have distinct IDs but ``agents add/delete`` rewrite one shared config.
Keep Plan's existing lock path and overrides so all callers, including an older
Plan process, coordinate. Hold the lock around the complete CLI command, never
around model execution. The lock file must not be unlinked after release.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time


@contextmanager
def registration_lock():
    # Preserve the deployed Plan settings; do not introduce a second lock.
    lock_path = Path(os.environ.get(
        "CLAWEVOLVE_PLAN_AGENT_REGISTRATION_LOCK",
        "/tmp/clawevolve-openclaw-agent-registration.lock",
    ))
    timeout = int(os.environ.get("CLAWEVOLVE_PLAN_AGENT_LOCK_TIMEOUT", "60"))
    with file_lock(lock_path, timeout_seconds=timeout):
        yield


@contextmanager
def file_lock(lock_path: Path, *, timeout_seconds: float):
    """Plan's bounded process lock, also used for its per-agent lifecycle."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for OpenClaw lock: {lock_path}")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
