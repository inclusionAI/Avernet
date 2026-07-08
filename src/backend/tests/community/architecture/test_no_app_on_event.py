"""Forbid ``@app.on_event(...)`` anywhere under ``src/agentclaw/``.

Rule 11 (Lifecycle is Uniform and Enforced) requires every startup /
shutdown action to flow through the ``Lifecycle`` Protocol and run
inside ``_app_lifespan`` (the FastAPI lifespan in ``adapters/http/app.py``).
The legacy ``@app.on_event("startup")`` / ``@app.on_event("shutdown")``
decorators are also officially deprecated by FastAPI in favour of
lifespan handlers — so we forbid them outright.

Detection: AST scan. Any decorator of the form ``@<name>.on_event(...)``
under ``src/agentclaw/`` is a violation, regardless of the receiver
identifier. The handler must move to a ``Lifecycle.startup()`` /
``Lifecycle.shutdown()`` on a DI-managed component instead.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]               # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


def _is_on_event_decorator(node: ast.AST) -> bool:
    """True iff ``node`` looks like ``@<x>.on_event(...)``.

    Matches both the decorator-call form (``@foo.on_event("startup")``)
    and the bare attribute form (``@foo.on_event``), since either
    creates the deprecated lifecycle handler.
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute) and node.attr == "on_event":
        return True
    return False


def _on_event_lines(tree: ast.Module) -> list[int]:
    """Return line numbers of every ``@*.on_event(...)`` decorator."""
    hits: list[int] = []
    for node in ast.walk(tree):
        decorators = getattr(node, "decorator_list", None)
        if not decorators:
            continue
        for dec in decorators:
            if _is_on_event_decorator(dec):
                hits.append(dec.lineno)
    return hits


@pytest.mark.unit
def test_no_app_on_event_decorators_under_agentclaw() -> None:
    """Forbid ``@app.on_event(...)`` anywhere under ``src/agentclaw/``.

    Lifecycle work belongs on ``Lifecycle.startup()`` /
    ``Lifecycle.shutdown()`` of a DI-managed component, which the
    composition root's ``_app_lifespan`` discovers and dispatches.
    """
    violations: list[str] = []
    for path in _AGENTCLAW_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for lineno in _on_event_lines(tree):
            rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
            violations.append(f"{rel}:{lineno}  @*.on_event(...) decorator")
    assert not violations, (
        "@app.on_event(...) is forbidden — Lifecycle hooks must live on "
        "DI-managed components implementing kernel.lifecycle.Lifecycle.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )
