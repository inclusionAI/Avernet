#!/usr/bin/env python3
"""Unified entry point — selects SOFA or bare mode via ``--mode`` argument.

Usage::

    # SOFA mode (default)
    python src/secbaas/community/main.py --config configs/ --mode sofa

    # Bare mode (no MOSN/Layotto)
    python src/secbaas/community/main.py --config configs/ --mode bare

Environment variables:
    SERVER_ENV             — env overlay (dev, prepub, prod, etc.)
    SOFAPY_CONFIG_OVERLAY  — overlay YAML to merge on top of base config
"""

from __future__ import annotations

import argparse
import os
import sys

MODE_SOFA = "sofa"
MODE_BARE = "bare"

if __name__ == "__main__":
    # Fix "Bad File Descriptor" when uvicorn spawns multi-worker processes.
    # Uvicorn captures sys.stdin.fileno() and passes the fd to spawned children,
    # which call os.fdopen(stdin_fileno). On macOS where multiprocessing uses
    # "spawn", fd 0 is not inheritable across exec, so fdopen(0) fails.
    #
    # Setting sys.stdin to None makes sys.stdin.fileno() raise AttributeError,
    # which uvicorn's get_subprocess() catches (AttributeError, OSError) and
    # handles by setting stdin_fileno=None, skipping the os.fdopen(0) entirely.
    #
    # Two levels:
    # 1. Set sys.stdin = None here for the current process (bare mode).
    # 2. Set PYTHONPATH with sitecustomize.py that does the same, so every
    #    spawned Python child also picks this up before uvicorn runs.
    sys.stdin = None

    _sitecustomize_dir = os.path.dirname(__file__)
    _sitecustomize_dir = os.path.abspath(_sitecustomize_dir)
    _existing_pythonpath = os.environ.get("PYTHONPATH", "")
    if _existing_pythonpath:
        os.environ["PYTHONPATH"] = (
            f"{_sitecustomize_dir}{os.pathsep}{_existing_pythonpath}"
        )
    else:
        os.environ["PYTHONPATH"] = _sitecustomize_dir

    parser = argparse.ArgumentParser(description="secbaas entry point")
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Configuration file path",
    )
    parser.add_argument(
        "--mode",
        "-m",
        type=str,
        default=MODE_BARE,
        choices=[MODE_SOFA, MODE_BARE],
        help="Runtime mode: sofa (default) or bare",
    )
    args = parser.parse_args()

    config_path = args.config or ""

    # Discover runner via entry_points — no hardcoded enterprise import.
    # Enterprise registers "sofa" runner; community registers "bare" runner.
    from importlib.metadata import entry_points

    runners = entry_points(group="secbaas.runner")
    matching = [ep for ep in runners if ep.name == args.mode]
    if not matching:
        raise RuntimeError(f"No runner registered for mode: {args.mode}")
    runner_cls = matching[0].load()
    runner_cls().run(config_path)
