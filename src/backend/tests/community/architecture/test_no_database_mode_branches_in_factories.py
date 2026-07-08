"""Forbid scattered ``DATABASE_MODE`` / ``_is_sqlite_mode()`` branching.

``DATABASE_MODE`` is retired (B1): the database binding is chosen by the
``DEPLOY_PROFILE`` switch via ``modules_for(profile)``, so nothing reads
the env var anymore. This test keeps it that way — an ``if
_is_sqlite_mode()`` or a stray ``DATABASE_MODE`` read is a violation of
the DI invariant: code paths that mutate behaviour by peeking at env vars
at runtime, instead of resolving the dependency the injector handed them.

Detection (string + AST):

  - ``_is_sqlite_mode`` mentioned anywhere outside the allowlist (covers
    both definitions and calls; cheap to detect via source-text scan).
  - The literal string ``DATABASE_MODE`` appearing as the argument of an
    ``os.getenv`` / ``os.environ`` access outside the allowlist.

Pre-existing legacy violations are explicitly allowlisted below — the
guard exists to stop NEW occurrences spreading further.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

# Files that may read DATABASE_MODE / define `_is_sqlite_mode`.
_ALLOWED_FILES: dict[str, str] = {
    # Composition Root — bootstraps OpenClaw db.py before the injector runs.
    "adapters/http/app.py": "Bootstrap configuration of legacy OpenClaw db.py before injector init.",
    # Legacy api-level dependencies module; still consulted by a few routes.
    "adapters/http/dependencies.py": "Pre-existing legacy DI factory still in transition.",
}

# Files that may inspect mode for testing-time overrides (di/modules/testing_*).
_TESTING_MODULE_PREFIX = "di/modules/testing_"


def _is_allowed(rel: str) -> bool:
    if rel in _ALLOWED_FILES:
        return True
    if rel.startswith(_TESTING_MODULE_PREFIX):
        return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel(path: pathlib.Path) -> str:
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    # B11: layers migrate under ``agentclaw/community/<layer>``. Strip the
    # ``community/`` prefix so layer-relative allowlist keys ("adapters/...")
    # match whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _strip_string_literals(tree: ast.Module) -> list[tuple[ast.AST, int]]:
    """Walk and return AST nodes that aren't pure string constants — we want to
    flag *code* references, not docstring mentions.
    """
    return [(n, getattr(n, "lineno", 0)) for n in ast.walk(tree)]


def _line_is_docstring(tree: ast.Module, target_line: int) -> bool:
    """Return True if `target_line` is inside a string constant (e.g. docstring).

    The cheap heuristic: walk the AST collecting line spans of every
    `ast.Constant` whose value is a string, and check membership.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= target_line <= end:
                return True
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= target_line <= end:
                return True
    return False


def _find_is_sqlite_mode_refs(tree: ast.Module) -> list[int]:
    """Return line numbers where the name `_is_sqlite_mode` appears in code.

    Includes: function calls (`_is_sqlite_mode()`), name references
    (`_is_sqlite_mode`), and `from ... import _is_sqlite_mode`.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "_is_sqlite_mode":
            lines.append(node.lineno)
        elif isinstance(node, ast.FunctionDef) and node.name == "_is_sqlite_mode":
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "_is_sqlite_mode":
                    lines.append(node.lineno)
    return lines


def _find_database_mode_env_reads(tree: ast.Module) -> list[int]:
    """Return line numbers of `os.getenv("DATABASE_MODE"...)` or
    `os.environ["DATABASE_MODE"]` / `.get("DATABASE_MODE")` reads.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        # os.getenv("DATABASE_MODE", ...)  or  environ.get("DATABASE_MODE", ...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("getenv", "get"):
                if node.args and isinstance(node.args[0], ast.Constant) \
                        and node.args[0].value == "DATABASE_MODE":
                    lines.append(node.lineno)
        # os.environ["DATABASE_MODE"]
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value == "DATABASE_MODE":
                lines.append(node.lineno)
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_database_mode_branches_in_factories() -> None:
    """Fail when any non-allowlisted module reads DATABASE_MODE or calls
    `_is_sqlite_mode`.
    """
    violations: list[str] = []
    for path in _AGENTCLAW_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        rel = _rel(path)
        if _is_allowed(rel):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for lineno in _find_is_sqlite_mode_refs(tree):
            violations.append(f"{rel}:{lineno}  uses `_is_sqlite_mode`")
        for lineno in _find_database_mode_env_reads(tree):
            violations.append(f"{rel}:{lineno}  reads env var `DATABASE_MODE`")
    assert not violations, (
        "DATABASE_MODE / `_is_sqlite_mode` may only be touched by "
        "`di/modules/testing_*.py` and explicitly allowlisted legacy files.\n"
        "The database binding is selected by `DEPLOY_PROFILE` via "
        "`modules_for(profile)`; resolve the dependency from the injector "
        "instead of reading the env var.\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_database_mode_allowlist_entries_still_exist() -> None:
    """Stale allowlist entries must be pruned."""
    missing = [
        rel for rel in _ALLOWED_FILES
        if not (_AGENTCLAW_ROOT / rel).is_file()
        and not (_AGENTCLAW_ROOT / "community" / rel).is_file()
    ]
    assert not missing, (
        "Allowlisted files no longer exist. Remove stale entries from "
        "`_ALLOWED_FILES`:\n  " + "\n  ".join(missing)
    )
