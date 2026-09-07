#!/usr/bin/env python3
"""Resolve startup cwd before touching saved configuration or running services."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import sys

import psutil

from engine.community.config import default_claude_code_workspace

KEYS = ("CLAUDE_CODE_DEFAULT_CWD", "RELAY_DEFAULT_CWD", "OPENCLAW_WORKSPACE_DIR")


def validate(value: str) -> str:
    path = Path(value)
    if (
        not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or any(c in value for c in ("\n", "\r", "\x00", "$", "`"))
    ):
        raise ValueError("Claude Code cwd must be a literal absolute non-root path")
    return str(path)


def saved_values(path: Path) -> list[str]:
    values = []
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip().removeprefix("export ")
        if not any(stripped.startswith(key + "=") for key in KEYS):
            continue
        parts = shlex.split(stripped)
        if len(parts) != 1:
            raise ValueError("Invalid saved Claude Code cwd assignment")
        value = parts[0].split("=", 1)[1]
        if value:
            values.append(validate(value))
    return values


def resolve(home: Path, env: dict[str, str], pids: list[int]) -> str:
    values = [validate(env[key]) for key in KEYS if env.get(key)]
    paths = [home / ".adaptorEnv", home / ".relayEnv"]
    for path in paths:
        values.extend(saved_values(path))
    # A running process may predate the env files on disk. Validate its actual
    # inherited values before a repeated startup can overwrite those files.
    for pid in pids:
        if pid <= 0:
            continue
        process = psutil.Process(pid)
        # Supervisor may own bash/run.sh rather than the actual worker. Shell
        # /proc environ can predate `source`; inspect the service descendants.
        if process.name().startswith("python") or process.name() == "node":
            workers = [process]
        else:
            workers = [
                child
                for child in process.children(recursive=True)
                if any(
                    arg.endswith(("start.py", "server.js")) for arg in child.cmdline()
                )
            ]
        if not workers:
            raise ValueError("Cannot identify the running Claude service process")
        for worker in workers:
            process_env = worker.environ()
            observed = [
                validate(process_env[key]) for key in KEYS if process_env.get(key)
            ]
            if not observed:
                raise ValueError(
                    "Running Claude process has no verifiable cwd configuration"
                )
            values.extend(observed)
    if not values and env.get("CLAUDE_CODE_INITIAL_CWD"):
        if any(path.exists() for path in paths):
            raise ValueError("Existing configuration lacks cwd; supply an explicit cwd")
        initial = env["CLAUDE_CODE_INITIAL_CWD"]
        values.append(
            str(default_claude_code_workspace(home))
            if initial == "default"
            else validate(initial)
        )
    distinct = set(values)
    if len(distinct) > 1:
        raise ValueError(
            "Conflicting Claude Code cwd configuration; reconcile saved and explicit values"
        )
    if not distinct:
        raise ValueError(
            "Claude Code cwd is unknown; supply CLAUDE_CODE_DEFAULT_CWD explicitly"
        )
    return distinct.pop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--running-pid", type=int, action="append", default=[])
    args = parser.parse_args()
    try:
        print(resolve(args.home, dict(os.environ), args.running_pid))
    except (ValueError, OSError, psutil.Error) as exc:
        print(f"Claude workspace configuration error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
