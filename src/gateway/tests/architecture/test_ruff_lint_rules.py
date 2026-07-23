"""Architecture enforcement: ruff lint & format rules.

Lint rules are enforced as an architecture gate — every ``just test-arch``
run verifies:

- **Lint** (``ruff check .``) — rules in ``pyproject.toml`` (E, F, I, N, UP, ASYNC)
- **Format** (``ruff format --check .``) — catches formatting drift.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(args: list[str], label: str) -> None:
    """Run ruff with *args* and raise on non-zero exit."""
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
    """Run ``ruff format --check .`` — format enforcement."""
    _run_ruff(["format", "--check", "."], "format")
