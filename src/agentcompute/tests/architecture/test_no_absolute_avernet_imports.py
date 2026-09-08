"""Architecture test: no absolute import of _avernet outside agents/."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agentcompute"
AGENTS_DIR = SRC / "community" / "plugins" / "agents"


def test_no_absolute_avernet_imports() -> None:
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            path.relative_to(AGENTS_DIR)
            continue
        except ValueError:
            pass
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level > 0:
                continue
            if node.module is None:
                continue
            if "_avernet" in node.module:
                violations.append(
                    f"{path.relative_to(SRC)}: absolute import of _avernet "
                    f"(from {node.module}) — use relative import or public re-export"
                )
    assert not violations, "\n".join(violations)
