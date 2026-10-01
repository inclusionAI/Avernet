"""Architecture guard — ExpertChat does not branch on concrete engine strings.

ExpertChat may normalize an engine identity for routing/logging, but engine-specific
chat-session behavior must be declared by the registered engine strategy. This keeps
new relay-managed engines from adding new ``if engine_type == "..."`` branches in
the core chat service.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[3]
_EXPERT_CHAT_ROOT = (
    _BACKEND_ROOT / "src" / "agentclaw" / "community" / "core" / "expert_chat"
)
_ENGINE_LITERALS = frozenset({"aicoding", "claude_code", "openclaw"})


def _is_engine_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in _ENGINE_LITERALS


def _iter_source_files():
    for path in _EXPERT_CHAT_ROOT.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


class _EngineStringBranchVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        has_engine_literal = _is_engine_literal(node.left) or any(
            _is_engine_literal(comparator) for comparator in node.comparators
        )
        has_equality_op = any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
        if has_engine_literal and has_equality_op:
            self.violations.append((node.lineno, "compares against a concrete engine string"))
        self.generic_visit(node)


def test_expert_chat_does_not_branch_on_engine_strings() -> None:
    failures: list[str] = []
    for path in _iter_source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError):
            continue
        visitor = _EngineStringBranchVisitor()
        visitor.visit(tree)
        for lineno, what in visitor.violations:
            rel = path.relative_to(_BACKEND_ROOT)
            failures.append(f"{rel}:{lineno} — {what}")

    if failures:
        pytest.fail(
            "ExpertChat must ask engine strategies for chat-session behavior "
            "instead of branching on concrete engine strings:\n  "
            + "\n  ".join(failures)
        )
