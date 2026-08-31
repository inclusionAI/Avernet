"""Arch gate: a Protocol base never shadows a real implementation.

A concrete service inherits its Service API Protocol so the contract is
navigable and a missing member fails loudly. But a Protocol's members are
``...`` stubs, so where the class *also* inherits a mixin or base that
carries real implementations, base order decides which one wins the MRO.
Put the Protocol first and its stubs silently shadow the working code —
every shadowed method starts returning ``None``.

That is not hypothetical: it shipped once. ``CollaboratorService`` was
written as ``class CollaboratorService(CollaboratorServiceProtocol,
CollaboratorQueryMixin)``, which made ``/api/bot/collaborator/list``
answer 500 instead of 404 and collaborator lock-info report nothing at
all — while ``hasattr`` and ``issubclass`` both still passed, because the
attribute existed either way.

The rule: in a non-Protocol class, a Protocol base must not precede a
non-Protocol base that defines any of the same members. Ordering alone is
not flagged — two plugin classes list a Protocol first but share no member
with their mixin, so nothing is shadowed and nothing is broken.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]  # .../src/backend
_COMMUNITY_ROOT = _BACKEND_ROOT / "src" / "agentclaw" / "community"


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return None


def _members(cls: ast.ClassDef) -> set[str]:
    return {
        n.name
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("__")
    }


def _index() -> tuple[set[str], dict[str, set[str]]]:
    """Protocol class names, and every class's directly-defined members."""
    protocols: set[str] = {"Protocol"}
    members: dict[str, set[str]] = {}
    for file in _COMMUNITY_ROOT.rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        try:
            tree = ast.parse(file.read_text(), filename=str(file))
        except SyntaxError:  # pragma: no cover — a separate failure
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            members.setdefault(node.name, set()).update(_members(node))
            if any(_base_name(b) == "Protocol" for b in node.bases):
                protocols.add(node.name)
    return protocols, members


@pytest.mark.unit
def test_protocol_bases_come_last() -> None:
    protocols, members = _index()
    offenders: list[str] = []

    for file in _COMMUNITY_ROOT.rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        try:
            tree = ast.parse(file.read_text(), filename=str(file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or len(node.bases) < 2:
                continue
            names = [_base_name(b) for b in node.bases]
            # A Protocol *definition* (``class X(Y, Protocol)``) is exempt:
            # every base there is a contract, so nothing can be shadowed.
            if "Protocol" in names:
                continue
            kinds = [n in protocols for n in names]
            own = _members(node)
            for i, is_proto in enumerate(kinds):
                if not is_proto:
                    continue
                stubs = members.get(names[i], set()) - own
                for later, later_is_proto in zip(names[i + 1:], kinds[i + 1:]):
                    if later_is_proto:
                        continue
                    clash = sorted(stubs & members.get(later, set()))
                    if clash:
                        rel = file.relative_to(_COMMUNITY_ROOT)
                        offenders.append(
                            f"{rel}:{node.lineno} class {node.name}"
                            f"({', '.join(str(n) for n in names)}) — "
                            f"Protocol '{names[i]}' shadows {later}"
                            f"{clash[:5]}"
                        )

    assert not offenders, (
        "A Protocol base must come after any non-Protocol base that defines "
        "the same members, or the Protocol's `...` stubs win the MRO and "
        "those methods start returning None:\n  " + "\n  ".join(offenders)
    )
