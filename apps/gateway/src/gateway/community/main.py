#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

MODE_SOFA = "sofa"
MODE_BARE = "bare"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="gateway entry point")
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
        default=MODE_BARE,
        choices=[MODE_SOFA, MODE_BARE],
        help="Runtime mode: bare (default, open-source) or sofa (enterprise)",
    )
    args = parser.parse_args(argv)

    config_path = args.config or ""

    # Discover runner via entry_points — no hardcoded enterprise import.
    # Enterprise registers the "sofa" runner; community registers "bare".
    from importlib.metadata import entry_points

    runners = entry_points(group="gateway.runner")
    matching = [ep for ep in runners if ep.name == args.mode]
    if not matching:
        raise RuntimeError(
            f"No runner registered for mode: {args.mode!r}. "
            "Install gateway-enterprise for 'sofa' mode."
        )
    runner_cls = matching[0].load()
    runner_cls().run(config_path)


if __name__ == "__main__":
    # Fix "Bad File Descriptor" when uvicorn spawns multi-worker processes.
    # Uvicorn captures sys.stdin.fileno() and passes the fd to spawned
    # children, which call os.fdopen(stdin_fileno). On macOS (spawn), fd 0
    # is not inheritable across exec, so fdopen(0) fails. Setting
    # sys.stdin to None makes fileno() raise AttributeError, which uvicorn
    # catches and handles by setting stdin_fileno=None.
    sys.stdin = None  # type: ignore[assignment]
    main()
