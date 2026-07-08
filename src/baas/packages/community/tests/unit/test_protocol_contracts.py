"""Enforce Rule 25 -- every SPI contract test file exists and has test functions."""

from __future__ import annotations

import ast
import pathlib

import pytest

_SPI_DIR = pathlib.Path(__file__).resolve().parents[1] / "contract" / "spi"

_EXPECTED_CONTRACT_FILES: set[str] = {
    "test_cache_plugin.py",
    "test_crypto_plugin.py",
    "test_database_plugin.py",
    "test_docker_sandbox_plugin.py",
    "test_k8s_sandbox_plugin.py",
    "test_poolab_sandbox_plugin.py",
    "test_scheduler_plugin.py",
}


def _suite_has_test(path: pathlib.Path) -> bool:
    """True iff the suite file defines at least one ``test_*`` function.

    Checks top-level functions and methods inside class bodies, since contract
    test files use class-based pytest tests (e.g. ``Contract`` base classes
    with ``test_*`` methods).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return False

    def _is_test(node: ast.AST) -> bool:
        return isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_")

    for node in tree.body:
        if _is_test(node):
            return True
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if _is_test(item):
                    return True
    return False


def test_every_spi_contract_suite_exists_and_has_tests() -> None:
    """Verify all expected SPI contract test files exist and contain test functions."""
    failures: list[str] = []

    for filename in sorted(_EXPECTED_CONTRACT_FILES):
        suite = _SPI_DIR / filename
        if not suite.is_file():
            failures.append(f"Missing contract test: tests/contract/spi/{filename}")
            continue
        if not _suite_has_test(suite):
            failures.append(
                f"Contract test has no test_* function: tests/contract/spi/{filename}"
            )

    if failures:
        pytest.fail("\n".join(failures))
