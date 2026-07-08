"""Architecture enforcement: ruff lint & format rules.

Lint rules are enforced as an architecture gate — every ``just test-arch``
run verifies:

- **Lint** (``ruff check .``) — rules in ``pyproject.toml`` (E, F, I, N, UP, ASYNC)
- **Import sort + format** (``ruff check --select I --fix . && ruff format .``) —
  catches import ordering violations and formatting drift.

This ensures regressions are caught in CI, not just by ad-hoc
``just check-lint`` / ``just check-format`` invocations.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(args: list[str], label: str) -> None:
    """Run ruff with ``args`` and raise on non-zero exit.

    Displays ruff's output on failure for actionable diagnostics.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ruff"] + args,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        diagnostics = stdout or stderr
        raise AssertionError(
            f"ruff {label} failed (exit code {result.returncode}):\n{diagnostics}"
        )


def test_ruff_lint_passes() -> None:
    """Run ``ruff check .`` — project-wide lint enforcement."""
    _run_ruff(["check", "."], "check")


def test_ruff_formatting_passes() -> None:
    """Run ``ruff check --select I --fix . && ruff format .``.

    The ``--fix`` flag is included so import-sort violations are
    auto-corrected.  The format check runs with ``--check`` to
    avoid modifying files during tests.
    """
    _run_ruff(["check", "--select", "I", "."], "import-sort (select I)")
    _run_ruff(["format", "--check", "."], "format")
