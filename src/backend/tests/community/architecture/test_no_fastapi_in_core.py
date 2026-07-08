"""Forbid any ``fastapi`` / ``starlette`` / ``fastapi_injector`` import
anywhere under ``src/agentclaw/core/``.

Rule 7 (``docs/arch/arch.rules.md``) — Core Independence — requires
``core/`` to be a framework-free library. Transport / DI-glue concerns
belong in the ``api/`` adapter layer. This widens the earlier narrow
``HTTPException``-only guard to enforce the full rule: any import of a
FastAPI / Starlette symbol — or the ``fastapi_injector`` DI bridge —
under ``core/`` is a violation.

Detection: AST walk over every ``.py`` under ``core/``, flagging both
``ImportFrom`` and ``Import`` nodes whose top-level module matches a
forbidden prefix.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
# B11: the core layer now lives under BOTH community/ and corp/. Rule 7
# (core is framework-free — no fastapi/starlette) is universal, so scan both.
_CORE_ROOTS = (
    _BACKEND_ROOT / "src" / "agentclaw" / "community" / "core",
    _BACKEND_ROOT / "src" / "agentclaw" / "corp" / "core",
)


# Top-level module prefixes that are forbidden under core/.
# A module matches if it equals the prefix or starts with "<prefix>.".
_FORBIDDEN_PREFIXES = ("fastapi", "starlette")
# Exact module names that are forbidden (separate from prefix list to avoid
# accidentally catching unrelated future names).
_FORBIDDEN_EXACT = frozenset({"fastapi_injector"})


# Temporary allowlist of files still being migrated as part of the Rule 7
# widening (see specs/2026-05-21-rule7-httpexception-out-of-core/). Entries
# are removed as each group of the migration lands; the goal is an empty set.
# Paths are relative to src/backend/.
_ALLOWLIST: frozenset[str] = frozenset({
    # Interceptor module uses FastAPI Response/Request for permission checking.
    # This is adapter-shaped code that needs HTTP primitives.
    "src/agentclaw/community/core/bot_collaborator/interceptor/base.py",
})


def _is_forbidden(module: str) -> bool:
    if not module:
        return False
    if module in _FORBIDDEN_EXACT:
        return True
    for prefix in _FORBIDDEN_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def _iter_core_py_files() -> list[pathlib.Path]:
    return sorted(
        p
        for root in _CORE_ROOTS
        if root.is_dir()
        for p in root.rglob("*.py")
        if p.is_file()
    )


def _find_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return [(lineno, module), ...] for forbidden imports in path."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:  # pragma: no cover
        pytest.fail(f"Could not parse {path}: {e}")

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden(module):
                hits.append((node.lineno, f"from {module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


def test_no_fastapi_imports_in_core():
    """Every .py file under core/ must be free of FastAPI/Starlette imports."""
    offenders: list[str] = []
    stale_allowlist: list[str] = []
    for path in _iter_core_py_files():
        rel = path.relative_to(_BACKEND_ROOT)
        rel_posix = rel.as_posix()
        violations = _find_violations(path)
        if rel_posix in _ALLOWLIST:
            if not violations:
                stale_allowlist.append(rel_posix)
            continue
        for lineno, snippet in violations:
            offenders.append(f"  {rel}:{lineno}  {snippet}")

    if stale_allowlist:
        pytest.fail(
            "Stale entries in _ALLOWLIST (no longer have violations — remove them):\n"
            + "\n".join(f"  {p}" for p in stale_allowlist)
        )

    if offenders:
        msg = (
            "fastapi / starlette / fastapi_injector imports are forbidden "
            "under src/agentclaw/core/ (Rule 7 — Core Independence).\n"
            "Move adapter-shaped code (routers, FastAPI Depends shims, "
            "fastapi_injector.Injected) to api/. Refactor service signatures "
            "that take UploadFile to take (data: bytes, filename: str, "
            "content_type: str | None) instead. Use asyncio.to_thread in "
            "place of starlette.concurrency.run_in_threadpool.\n\n"
            f"Offending imports ({len(offenders)} total):\n"
            + "\n".join(offenders)
        )
        pytest.fail(msg)
