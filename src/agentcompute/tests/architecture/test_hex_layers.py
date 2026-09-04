"""Architecture enforcement: hex-layer boundaries in the community package.

With the demo collapsed into a single ``community`` package (no separate
enterprise layer), the invariants are:

1. ``spi`` is leaf-level: it imports nothing concrete (no core/adapters/plugins).
2. ``core`` (domain) imports ``spi`` only — never adapters or plugins.
3. ``plugins`` (implementations) import ``spi`` / ``core`` but never ``adapters``
   (IO ports), except the runner which is the composition root.
4. ``adapters`` never import ``plugins`` at module top level (composition edge).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "agentcompute"


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.name != "__pycache__")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


@pytest.mark.parametrize(
    "path",
    _py_files(SRC / "spi"),
    ids=lambda p: str(p.relative_to(SRC)),
)
def test_spi_is_leaf_level(path: Path) -> None:
    for module in _imported_modules(path):
        assert not module.startswith("agentcompute.community.core"), (
            f"{path.relative_to(SRC)} imports core module {module!r}."
        )
        assert not module.startswith("agentcompute.community.adapters"), (
            f"{path.relative_to(SRC)} imports adapter module {module!r}."
        )
        assert not module.startswith("agentcompute.community.plugins"), (
            f"{path.relative_to(SRC)} imports plugin module {module!r}."
        )


@pytest.mark.parametrize(
    "path",
    _py_files(SRC / "core"),
    ids=lambda p: str(p.relative_to(SRC)),
)
def test_core_imports_spi_only(path: Path) -> None:
    for module in _imported_modules(path):
        assert not module.startswith("agentcompute.community.adapters"), (
            f"{path.relative_to(SRC)} imports adapter module {module!r}."
        )
        assert not module.startswith("agentcompute.community.plugins"), (
            f"{path.relative_to(SRC)} imports plugin module {module!r}."
        )


@pytest.mark.parametrize(
    "path",
    [p for p in _py_files(SRC / "plugins") if "/runner/" not in str(p)],
    ids=lambda p: str(p.relative_to(SRC)),
)
def test_plugins_never_import_adapters(path: Path) -> None:
    for module in _imported_modules(path):
        assert not module.startswith("agentcompute.community.adapters"), (
            f"{path.relative_to(SRC)} imports adapter module {module!r}; "
            "plugins must not depend on IO adapters (the runner is the "
            "composition root and is exempt)."
        )


def test_adapters_never_import_plugins_at_top_level() -> None:
    direct = []
    for path in _py_files(SRC / "adapters"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules = [node.module] if node.module else []
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            if any(m.startswith("agentcompute.community.plugins") for m in modules):
                direct.append((path.name, modules))
    assert direct == []
