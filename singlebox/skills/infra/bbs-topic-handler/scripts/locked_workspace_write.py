#!/usr/bin/env python3
"""Safely update one file in an OpenClaw workspace under a process-shared flock.

The caller performs semantic extraction. This helper only provides deterministic
concurrency control, exact-content idempotency, compare-and-swap replacement,
and atomic durable writes.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

LOCK_NAME = ".bbs-persistence.lock"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_target(workspace: Path, target_arg: str) -> Path:
    workspace = workspace.resolve()
    target = Path(target_arg)
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve(strict=False)
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("target must stay inside workspace") from exc
    return target


def contains_exact_block(current: str, block: str) -> bool:
    normalized = current.strip("\n")
    if not normalized:
        return False
    return (
        normalized == block
        or normalized.startswith(f"{block}\n\n")
        or normalized.endswith(f"\n\n{block}")
        or f"\n\n{block}\n\n" in normalized
    )


def acquire_lock(lock_path: Path, timeout: float) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise TimeoutError(f"timed out acquiring {lock_path}")
            time.sleep(0.05)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = None
    if path.exists():
        existing_mode = stat.S_IMODE(path.stat().st_mode)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        os.replace(temp_path, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_payload(args: argparse.Namespace) -> str:
    if args.content_file:
        return Path(args.content_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--target", required=True, help="workspace-relative target file")
    parser.add_argument("--operation", choices=("append", "replace"), required=True)
    parser.add_argument("--content-file")
    parser.add_argument("--expected-sha256", help="required for replacing an existing file")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("workspace must be an existing directory")
    target = resolve_target(workspace, args.target)
    payload = read_payload(args)
    if not payload:
        raise ValueError("content must not be empty")

    lock_fd = acquire_lock(workspace / LOCK_NAME, args.timeout)
    try:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        before_hash = sha256_text(current)

        if args.operation == "append":
            block = payload.strip("\n")
            if not block:
                raise ValueError("append content must not be blank")
            if contains_exact_block(current, block):
                result = {
                    "status": "already_exists",
                    "target": str(target.relative_to(workspace)),
                    "before_sha256": before_hash,
                    "after_sha256": before_hash,
                }
                print(json.dumps(result, ensure_ascii=False))
                return 0
            separator = (
                ""
                if not current or current.endswith("\n\n")
                else "\n"
                if current.endswith("\n")
                else "\n\n"
            )
            updated = f"{current}{separator}{block}\n"
        else:
            if target.exists() and not args.expected_sha256:
                raise ValueError("--expected-sha256 is required when replacing an existing file")
            if args.expected_sha256 and args.expected_sha256 != before_hash:
                result = {
                    "status": "conflict",
                    "target": str(target.relative_to(workspace)),
                    "expected_sha256": args.expected_sha256,
                    "actual_sha256": before_hash,
                }
                print(json.dumps(result, ensure_ascii=False))
                return 3
            updated = payload

        atomic_write(target, updated)
        verified = target.read_text(encoding="utf-8")
        if verified != updated:
            raise RuntimeError("post-write verification failed")
        result = {
            "status": "applied",
            "target": str(target.relative_to(workspace)),
            "before_sha256": before_hash,
            "after_sha256": sha256_text(verified),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # deterministic machine-readable failure
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
