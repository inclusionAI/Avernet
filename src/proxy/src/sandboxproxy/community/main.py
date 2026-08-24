#!/usr/bin/env python3
"""Process entrypoint — discovers a runner via entry points and runs it."""

from __future__ import annotations

import argparse
import sys
from typing import Any


def _load_runner(mode: str) -> Any:
    from importlib.metadata import entry_points

    for ep in entry_points(group="sandboxproxy.runner"):
        if ep.name == mode:
            return ep.load()()
    runners = entry_points(group="sandboxproxy.runner")
    raise RuntimeError(
        f"No runner registered for mode: {mode!r}. "
        f"Available: {', '.join(ep.name for ep in runners) or '(none)'}."
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="sandbox-proxy entry point")
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Configuration file or directory path",
    )
    parser.add_argument(
        "--mode",
        "-m",
        type=str,
        default="bare",
        choices=["bare"],
        help="Runtime mode: bare (default, open-source)",
    )
    args = parser.parse_args(argv)

    runner = _load_runner(args.mode)
    runner.run(args.config)


if __name__ == "__main__":
    # Fix "Bad File Descriptor" when uvicorn spawns multi-worker processes
    # (mirrors gateway's macOS spawn fix).
    sys.stdin = None
    main()
