"""Enforce that ``agentclaw.community.kernel`` is the lowest layer.

Every other layer (``api/``, ``core/``, ``plugin_api/``, ``plugins/``)
may import from ``agentclaw.community.kernel``. The kernel itself must not
import from any of them — it is the foundation everyone else stands
on.

Detection: AST-only. A violation is any ``import agentclaw.…`` or
``from agentclaw.… import …`` statement inside a file under
``src/agentclaw/kernel/``.

This guard means kernel code stays cheap to import (no transitive
plugin / DI bootstrap), and the dependency arrow always points
*into* kernel, never out.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_KERNEL_ROOT = _BACKEND_ROOT / "src" / "agentclaw" / "kernel"


def _agentclaw_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, name) for every import that names ``agentclaw.*``."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "agentclaw" or mod.startswith("agentclaw."):
                hits.append((node.lineno, f"from {mod} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agentclaw" or alias.name.startswith("agentclaw."):
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


@pytest.mark.unit
def test_kernel_imports_nothing_from_agentclaw() -> None:
    """``kernel/`` is the lowest layer — it imports no other ``agentclaw.*`` module."""
    violations: list[str] = []
    for path in _KERNEL_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        rel = path.relative_to(_KERNEL_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for lineno, what in _agentclaw_imports(tree):
            violations.append(f"kernel/{rel}:{lineno}  {what}")

    assert not violations, (
        "kernel/ is the lowest layer and must not import from agentclaw.*\n"
        "Move the contract being depended on into kernel/, or put the "
        "shared code somewhere both can reach.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )
