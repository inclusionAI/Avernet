"""Architecture enforcement: BAAS hybrid module-surface invariants.

Ports three canonical BAAS tests
(``../src/baas/tests/architecture/test_no_private_imports.py``,
``test_no_private_exports.py``, ``test_all_exports_valid.py``) to the
agentcompute package. The invariants are:

1. **No absolute private-module imports** — no ``.py`` file under
   ``src/agentcompute/`` may use ``from agentcompute._x.y import ...`` (an
   underscore-prefixed segment in an absolute in-repo import). Use the
   package's public re-export or a relative intra-package import instead.
   Relative imports (``level > 0``) are exempt; ``__init__.py`` re-exports
   rely on them.
2. **No underscore-prefixed names on the public surface** — no
   ``__init__.py`` under ``src/agentcompute/community/`` may expose an
   ``_``-prefixed name (excluding dunders) via ``__all__`` or a top-level
   ``Import`` / ``ImportFrom`` binding. Lazy imports inside function bodies
   are out of scope.
3. **``__all__`` is mandatory when ``__init__.py`` imports anything** — every
   ``__init__.py`` under ``src/agentcompute/community/`` with at least one
   top-level import must declare an ``__all__`` list enumerating the public
   surface. Empty or docstring-only ``__init__.py`` files are exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agentcompute"
COMMUNITY = SRC / "community"


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) >= 4


def _py_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py") if p.name != "__pycache__"
    )


def _init_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("__init__.py")
        if "__pycache__" not in p.parts
    )


def _has_all(tree: ast.AST) -> bool:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                ):
                    return True
    return False


def _top_level_imports(
    tree: ast.AST,
) -> list[ast.Import | ast.ImportFrom]:
    return [
        node
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def test_no_absolute_private_imports() -> None:
    violations: list[str] = []
    for path in _py_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level > 0:
                continue
            if node.module is None:
                continue
            if not node.module.startswith("agentcompute."):
                continue
            for segment in node.module.split("."):
                if segment.startswith("_") and not _is_dunder(segment):
                    violations.append(
                        f"{path.relative_to(SRC)}: absolute private-module "
                        f"import: from {node.module} import ... — use the "
                        "package's public re-export or a relative "
                        "intra-package import"
                    )
                    break
    assert not violations, (
        "absolute private-module imports found:\n" + "\n".join(violations)
    )


def test_no_private_exports() -> None:
    violations: list[str] = []
    for init_path in _init_files(COMMUNITY):
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        exposed: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not (isinstance(target, ast.Name) and target.id == "__all__"):
                    continue
                if not isinstance(node.value, ast.List):
                    continue
                for elt in node.value.elts:
                    if (
                        isinstance(elt, ast.Constant)
                        and isinstance(elt.value, str)
                    ):
                        exposed.append(elt.value)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    exposed.append(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    exposed.append(alias.asname or alias.name)
        for name in exposed:
            if name.startswith("_") and not _is_dunder(name):
                violations.append(
                    f"{init_path.relative_to(SRC)}: private name on public "
                    f"surface: {name!r} — remove from __all__ or move the "
                    "import inside a function body"
                )
    assert not violations, (
        "private names exposed on public surface:\n"
        + "\n".join(violations)
    )


def test_all_init_files_have_all() -> None:
    violations: list[str] = []
    for init_path in _init_files(COMMUNITY):
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        if not _top_level_imports(tree):
            continue
        if not _has_all(tree):
            violations.append(
                f"{init_path.relative_to(SRC)}: __init__.py has imports but "
                "no __all__ — add an __all__ list enumerating every public "
                "name the package exposes"
            )
    assert not violations, (
        "__init__.py files missing __all__:\n" + "\n".join(violations)
    )