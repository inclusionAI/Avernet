"""The materialisers do not know which engine family they write for (W8 D-7).

The delivery strategy hands them ports; the orchestrator hands them phases.
The moment a materialiser compares an engine string, the seam stops meaning
anything and the next family becomes a fork of five modules. Docstrings and
comments may *mention* an engine to explain a port's contract; code may not
name one.
"""
from __future__ import annotations

import ast
import pathlib

import agentclaw.community.core.bot_config_manifest.apply.materialisers as pkg

_ENGINE_WORDS = ("teclaw", "arca", "openclaw", "claude_code", "aicoding", "baas")


def _code_strings(tree: ast.AST) -> list[str]:
    """Every string constant that is not a docstring."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_materialiser_names_an_engine_in_code() -> None:
    root = pathlib.Path(pkg.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for value in _code_strings(tree):
            lowered = value.lower()
            if any(word in lowered for word in _ENGINE_WORDS):
                offenders.append(f"{path.name}: {value!r}")
        for node in ast.walk(tree):
            # ``is_teclaw`` / ``resolve_container_provider`` style lookups are
            # the engine question asked by another name.
            if isinstance(node, ast.Name) and node.id in {"is_teclaw", "is_teclaw_bot"}:
                offenders.append(f"{path.name}: name {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in {"is_teclaw", "is_teclaw_bot"}:
                offenders.append(f"{path.name}: attribute {node.attr}")
    assert offenders == [], "materialisers must not branch on the engine:\n" + "\n".join(offenders)
