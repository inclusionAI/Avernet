"""Atomic audit artifacts and per-run exclusion locks."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path, default: object) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


@contextmanager
def run_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "clawinsight-batch.lock"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink()


def freeze_request(path: Path, request: dict) -> dict:
    if path.exists():
        saved = json.loads(path.read_text())
        if saved["key"] != request["key"]:
            raise ValueError("outbox key mismatch")
        return saved  # Retry exactly the saved body, never regenerate a conflicting payload.
    write_json(path, request)
    return request
