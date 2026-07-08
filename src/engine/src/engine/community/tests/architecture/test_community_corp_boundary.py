"""Architecture guard: the community (open-source) runtime must not statically
import ``engine.corp``.

The community subtree is the open-source base. corp registers itself via a
string ``importlib.import_module("engine.corp...")`` at the composition root
(see ``community/di/profile_modules.py`` CORP branch + ``engine.corp.di.corp_bootstrap``),
so there is NO static ``community -> corp`` import edge and the community build
boots with ``engine/corp/`` physically absent.

This is an AST-based whole-subtree ratchet (stricter than the import-linter
``community-no-corp`` contract and free of grimp's plugin-node blind spot): it
walks every non-test ``community/**/*.py`` and flags any ``import``/``from``
statement naming ``engine.corp`` or ``engine.corp.*``. String literals passed to
``importlib.import_module(...)`` are invisible to the AST walk — that is the
sanctioned inversion and is intentionally allowed.
"""
from __future__ import annotations

import ast
from pathlib import Path

# this file: .../src/engine/src/engine/community/tests/architecture/<f>.py
# parents[2] == .../src/engine/src/engine/community  (the community package root)
_COMMUNITY_PKG = Path(__file__).resolve().parents[2]


def _community_runtime_py_files():
    for f in _COMMUNITY_PKG.rglob("*.py"):
        parts = f.parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        yield f


def _corp_import_offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            if name == "engine.corp" or name.startswith("engine.corp."):
                offenders.append(f"{path}:{node.lineno}: {name}")
    return offenders


def test_community_subtree_has_no_static_corp_imports():
    offenders: list[str] = []
    for f in _community_runtime_py_files():
        offenders.extend(_corp_import_offenders(f))
    assert not offenders, (
        "community (open-source) runtime must not statically import engine.corp; "
        "invert via import_module('engine.corp...') at the composition root:\n"
        + "\n".join(offenders)
    )
