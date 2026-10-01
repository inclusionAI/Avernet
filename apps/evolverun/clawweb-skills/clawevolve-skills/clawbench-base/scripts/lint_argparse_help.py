#!/usr/bin/env python3
"""Detect unescaped '%' in argparse help strings.

argparse performs old-style '%' interpolation on help text. A literal percent
must be written as '%%' or help rendering can raise ValueError.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


UNESCAPED_PERCENT = re.compile(r"(?<!%)%(?!(%|\())")  # skip %% and %(name)s
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__"}


class ArgparseHelpVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.errors: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (AST API method name)
        # Match parser.add_argument(..., help="...")-style calls.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for kw in node.keywords:
                if kw.arg != "help":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    help_text = kw.value.value
                    if UNESCAPED_PERCENT.search(help_text):
                        self.errors.append(
                            f"{self.path}:{kw.value.lineno}:{kw.value.col_offset + 1} "
                            f"unescaped '%' in argparse help string: {help_text!r}"
                        )
        self.generic_visit(node)


def _python_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        paths.append(path)
    return paths


def main() -> int:
    root = Path(".")
    errors: list[str] = []

    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            # Ignore unreadable/non-parseable files; CI will fail those elsewhere.
            continue

        visitor = ArgparseHelpVisitor(path)
        visitor.visit(tree)
        errors.extend(visitor.errors)

    if errors:
        print("Found argparse help formatting issues:")
        for err in errors:
            print(f" - {err}")
        return 1

    print("No argparse help '%' issues found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
