"""Architecture guards for Skill Center engine variability seams."""
from __future__ import annotations

import ast
import pathlib

import pytest


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_SKILL_CENTER_POLICIES = (
    _BACKEND_ROOT
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "skill_center"
    / "policies"
)
_ENGINE_POLICY_LITERALS = frozenset({"aicoding", "claude_code", "normalcc"})


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def test_skill_center_policies_do_not_branch_on_engine_literals() -> None:
    failures: list[str] = []
    for path in sorted(_SKILL_CENTER_POLICIES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            literals = _string_constants(node) & _ENGINE_POLICY_LITERALS
            if literals:
                failures.append(
                    f"{path.relative_to(_BACKEND_ROOT)}:{node.lineno} "
                    f"compares engine policy literal(s): {sorted(literals)}"
                )

    if failures:
        pytest.fail(
            "Skill Center policies must delegate engine-specific default "
            "SkillSet compatibility to registered engine resolvers, not branch "
            "on engine-name strings:\n  " + "\n  ".join(failures)
        )
