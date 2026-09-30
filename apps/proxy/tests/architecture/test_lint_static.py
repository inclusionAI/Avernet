"""Architecture enforcement: lint, format, and static type gates.

Runs the same gates as ``just check-*`` so regressions are caught in CI
(``test-arch``) rather than only through ad-hoc invocations:

- **Lint** (``ruff check .``) — rules in ``pyproject.toml`` (E, F, I, N, UP, ASYNC)
- **Import sort + format** (``ruff check --select I . && ruff format --check .``)
- **Static type check** (``mypy src``) — strict typing on the ``src`` tree
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], label: str) -> None:
    """Run ``cmd`` from the project root and raise on non-zero exit."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        diagnostics = (result.stdout or "").strip() or (result.stderr or "").strip()
        raise AssertionError(
            f"{label} failed (exit code {result.returncode}):\n{diagnostics}"
        )


class TestLintAndFormat:
    def test_ruff_lint_passes(self) -> None:
        """``ruff check .`` — project-wide lint enforcement."""
        _run([sys.executable, "-m", "ruff", "check", "."], "ruff check")

    def test_ruff_import_sort_passes(self) -> None:
        """``ruff check --select I .`` — import ordering enforcement."""
        _run(
            [sys.executable, "-m", "ruff", "check", "--select", "I", "."],
            "ruff import-sort (select I)",
        )

    def test_ruff_format_passes(self) -> None:
        """``ruff format --check .`` — formatting drift enforcement."""
        _run(
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            "ruff format",
        )


class TestStaticTyping:
    def test_mypy_src_passes(self) -> None:
        """``mypy src`` — strict type check on the source tree."""
        _run([sys.executable, "-m", "mypy", "src"], "mypy src")
