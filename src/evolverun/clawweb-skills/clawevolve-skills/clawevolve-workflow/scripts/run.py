#!/usr/bin/env python3
"""Validated entry point for multi-stage ClawEvolve workflows."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


def _reject_router_overrides(arguments: list[str]) -> None:
    """Keep handler routing and its fixed action owned by this entry point."""
    forbidden = ("--stage", "--task-id", "--step-id", "--action")
    for argument in arguments:
        if any(argument == name or argument.startswith(f"{name}=") for name in forbidden):
            raise SystemExit(f"workflow argument is controlled by router: {argument.split('=', 1)[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ClawEvolve workflow stage router", add_help=False)
    parser.add_argument("--stage", required=True, choices=("bench-plan", "optimize"))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    known, remaining = parser.parse_known_args()
    _reject_router_overrides(remaining)
    for name, value in (("task-id", known.task_id), ("step-id", known.step_id)):
        if not ID_PATTERN.fullmatch(value):
            raise SystemExit(f"invalid {name}")

    handlers = Path(__file__).resolve().parent / "handlers"
    if known.stage == "bench-plan":
        script = handlers / "clawevolve_bench_plan_run.py"
        prefix: list[str] = []
    else:
        script = handlers / "clawevolve_optimize_run.py"
        prefix = ["--action", "run-round"]
    if not script.is_file():
        raise SystemExit(f"workflow handler not found: {script.name}")
    os.execv(
        sys.executable,
        [sys.executable, "-u", "-B", str(script), *prefix,
         "--task-id", known.task_id, "--step-id", known.step_id, *remaining],
    )


if __name__ == "__main__":
    main()
