#!/usr/bin/env python3
"""Python entrypoint equivalent to ``scripts/run.sh``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)


def _require_supported_python() -> None:
    if sys.version_info < MIN_PYTHON:
        print(
            "clawevolve-plan requires Python >= 3.10.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    """Prepare the same runtime context as ``run.sh`` and invoke Plan."""

    _require_supported_python()
    invocation_cwd = Path.cwd()
    skill_dir = Path(__file__).resolve().parents[1]

    os.environ.setdefault("CLAWEVOLVE_INVOCATION_CWD", str(invocation_cwd))
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))

    from clawevolve_plan.cli import main as cli_main

    return cli_main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
