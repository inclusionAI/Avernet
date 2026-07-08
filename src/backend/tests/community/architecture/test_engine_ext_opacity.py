"""Architecture guard — ``engine_ext`` is opaque to the backend.

``BotConfigArtifact.engine_ext`` is an **engine-owned, free-form** blob: the
backend fetches it (via ``EngineExtClient``), freezes it with the version, and
hands it back to the engine **verbatim**. It must never *interpret* the payload —
no keying into it, no branching on its contents — because its shape is defined by
the engine, not us. (Kernel docstring: "must never interpret or branch on its
contents".)

This test enforces that statically: it AST-walks all production source and fails
if any code **reads into** an ``engine_ext`` value — i.e. subscripts it
(``x.engine_ext["k"]`` / ``engine_ext[i]``), calls a dict-reader on it
(``x.engine_ext.get(...)`` / ``.keys()`` / ``.items()`` / …), or iterates it
(``for k in x.engine_ext``).

Whole-value passthrough is explicitly allowed (that's the opaque contract):
``engine_ext=x.engine_ext``, ``dict(artifact.engine_ext)``,
``replace(a, engine_ext=...)``, ``data.get("engine_ext", {})`` — none of these
read the *contents*, so none are flagged.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]               # .../src/backend
_SRC_ROOT = _BACKEND_ROOT / "src" / "agentclaw"

# Dict/mapping readers that, called on an engine_ext value, would inspect contents.
_CONTENT_READERS = frozenset({
    "get", "keys", "values", "items", "pop", "setdefault",
    "__getitem__", "__contains__",
})


def _is_engine_ext_value(node: ast.AST) -> bool:
    """True iff ``node`` is an expression denoting an engine_ext *value* — either
    a ``*.engine_ext`` attribute access or a bare local named ``engine_ext``.

    NOT matched: the string literal ``"engine_ext"`` (a dict key) — reading the
    blob OUT of a dict (``data["engine_ext"]`` / ``data.get("engine_ext")``) hands
    back the whole value and is allowed.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "engine_ext"
    if isinstance(node, ast.Name):
        return node.id == "engine_ext"
    return False


class _OpacityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # x.engine_ext[...]  /  engine_ext[...]  — indexing into the blob.
        if _is_engine_ext_value(node.value):
            self.violations.append((node.lineno, "subscripts engine_ext"))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # x.engine_ext.get(...) / .keys() / .items() / ...  — reading contents.
        if node.attr in _CONTENT_READERS and _is_engine_ext_value(node.value):
            self.violations.append((node.lineno, f"calls .{node.attr} on engine_ext"))
        self.generic_visit(node)

    def _check_iter(self, iter_node: ast.AST, lineno: int) -> None:
        if _is_engine_ext_value(iter_node):
            self.violations.append((lineno, "iterates engine_ext"))

    def visit_For(self, node: ast.For) -> None:
        self._check_iter(node.iter, node.lineno)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:  # type: ignore[override]
        self._check_iter(node.iter, getattr(node.iter, "lineno", 0))
        self.generic_visit(node)


def _iter_source_files():
    for path in _SRC_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_engine_ext_is_never_read_into_by_backend() -> None:
    failures: list[str] = []
    for path in _iter_source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError):
            continue
        visitor = _OpacityVisitor()
        visitor.visit(tree)
        for lineno, what in visitor.violations:
            rel = path.relative_to(_BACKEND_ROOT)
            failures.append(f"{rel}:{lineno} — {what} (engine_ext must stay opaque)")

    if failures:
        pytest.fail(
            "engine_ext opacity violated — the backend must carry engine_ext "
            "verbatim, never interpret its contents:\n  " + "\n  ".join(failures)
        )
