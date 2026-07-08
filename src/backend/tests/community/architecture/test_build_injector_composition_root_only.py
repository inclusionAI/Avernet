"""``build_injector`` may only be called at the composition root.

The injector is the application's wiring graph: ``build_injector(...)``
constructs it from a ``DeployProfile`` plus the business module list.
Production code must call this **exactly once** — at the composition
root in ``adapters/http/app.py`` — and then hand the result to
``attach_injector(app, injector)`` so FastAPI routes can resolve
``Injected(X)`` parameters. Anyone else calling ``build_injector``
would be constructing a *second*, divorced injector with no relationship
to the running app's bindings — a recipe for "why is my Injected(X)
returning a different instance than `Foo()`?" debugging sessions.

This test enforces that boundary. Two checks:

1. **No file under ``src/agentclaw/`` may call ``build_injector(...)``**
   *except* ``adapters/http/app.py`` (the composition root) and
   ``di/container.py`` (the definition site).
2. **No file under ``src/agentclaw/`` may import ``build_injector``**
   except the same two — defense in depth against any future code
   pulling it in for a sneaky second injector.

Tests are free to call ``build_injector`` directly; per-test injectors
are how the test framework builds isolated environments. This test only
scans ``src/agentclaw/``.

Detection: AST-only. Direct calls (``build_injector(...)``) and
attribute calls (``mod.build_injector(...)``) are both flagged.
``from agentclaw.community.di import build_injector`` and equivalent attribute
imports are flagged separately so the failure message is precise.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Allowlist — files under src/agentclaw/ that may call or import
# ``build_injector``. Each entry MUST have a one-line justification.
# Adding a new entry is a review-level decision.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    "di/container.py": (
        "Definition site — defines build_injector itself."
    ),
    "di/__init__.py": (
        "Public surface — re-exports build_injector via __all__ so "
        "adapters/http/app.py can import it from agentclaw.community.di."
    ),
    "adapters/http/app.py": (
        "Composition root — calls build_injector once at module import "
        "to construct the app's injector, then hands it to "
        "attach_injector(app, injector). Single production caller by design."
    ),
}


_FORBIDDEN_NAME = "build_injector"


def _rel(path: pathlib.Path) -> str:
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    # B11: layers migrate under ``agentclaw/community/<layer>``. Strip the
    # ``community/`` prefix so layer-relative allowlist keys ("adapters/...")
    # match whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _is_build_injector_call(node: ast.AST) -> bool:
    """True iff ``node`` calls ``build_injector`` (bare or attribute)."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == _FORBIDDEN_NAME:
        return True
    if isinstance(fn, ast.Attribute) and fn.attr == _FORBIDDEN_NAME:
        return True
    return False


def _call_lines(tree: ast.Module) -> list[int]:
    return [
        node.lineno for node in ast.walk(tree) if _is_build_injector_call(node)
    ]


def _import_lines(tree: ast.Module) -> list[int]:
    """Lines that import ``build_injector`` via ``from ... import ...``."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _FORBIDDEN_NAME:
                    hits.append(node.lineno)
    return hits


@pytest.mark.unit
def test_build_injector_only_called_at_composition_root():
    """No file under ``src/agentclaw/`` may call ``build_injector``
    outside the allowlist (definition site + composition root).
    """
    failures: list[str] = []
    for py in _AGENTCLAW_ROOT.rglob("*.py"):
        rel = _rel(py)
        if rel in _ALLOWLIST:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for line in _call_lines(tree):
            failures.append(f"{rel}:{line} call {_FORBIDDEN_NAME}()")
        for line in _import_lines(tree):
            failures.append(f"{rel}:{line} import {_FORBIDDEN_NAME}")

    if failures:
        pytest.fail(
            "Found build_injector() references outside the composition root. "
            "build_injector() must be called exactly once, by "
            "adapters/http/app.py, to construct the app's injector. "
            "If you need a service, declare it as Injected(X) on your "
            "route or via @inject on your service constructor — never "
            "build a second injector:\n  "
            + "\n  ".join(failures)
        )


@pytest.mark.unit
def test_allowlist_paths_exist():
    """Stale allowlist entries hide future violations — prune them."""
    missing = [
        rel for rel in _ALLOWLIST
        if not (_AGENTCLAW_ROOT / rel).is_file()
        and not (_AGENTCLAW_ROOT / "community" / rel).is_file()
    ]
    if missing:
        pytest.fail(
            "Allowlist entries no longer point at real files. Remove them "
            "from _ALLOWLIST:\n  " + "\n  ".join(missing)
        )
