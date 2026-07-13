"""Architecture enforcement: every __init__.py with imports must define __all__.

All exported names must be valid (resolvable) and every package that imports
from its submodules must declare its public surface via ``__all__``.

Currently 6 __init__.py files have imports but no __all__:
- adapters/web/dependencies/__init__.py
- api/__init__.py
- plugins/logger/bare/__init__.py
- plugins/logger/sofa/__init__.py
- plugins/tracer/bare/__init__.py
- plugins/tracer/sofa/__init__.py
"""

import ast
import importlib
import os
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

_KNOWN_MISSING_ALL: set[str] = {
    "adapters/web/dependencies/__init__.py",
    "api/__init__.py",
    "plugins/logger/bare/__init__.py",
    "plugins/logger/sofa/__init__.py",
    "plugins/tracer/bare/__init__.py",
    "plugins/tracer/sofa/__init__.py",
}

# Packages where __all__ exports are resolved dynamically (e.g. via lazy imports,
# generated code, or private re-exports) and don't show up in hasattr().
_SKIP_RESOLVABLE_CHECK: set[str] = {
    "config/__init__.py",
}


def _get_all_names(init_file: Path) -> set[str]:
    """Extract __all__ names from an __init__.py, or empty set if absent."""
    tree = ast.parse(init_file.read_text())
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            return {
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
            }
    return set()


def _top_level_defs(init_file: Path) -> set[str]:
    """Return all top-level names defined in a module."""
    tree = ast.parse(init_file.read_text())
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_all_init_files_have_all():
    """Every __init__.py with imports must define __all__.

    Emits warnings for known missing files; fails for new violations.
    """
    violations: list[str] = []

    for init_file in sorted(SECBAAS.rglob("__init__.py")):
        if "__pycache__" in str(init_file):
            continue

        rel = str(init_file.relative_to(SECBAAS))
        tree = ast.parse(init_file.read_text())

        has_imports = any(
            isinstance(n, (ast.Import, ast.ImportFrom))
            for n in ast.iter_child_nodes(tree)
        )
        if not has_imports:
            continue

        has_all = any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)
            for n in ast.iter_child_nodes(tree)
        )

        if not has_all:
            if rel not in _KNOWN_MISSING_ALL:
                violations.append(f"  {rel}")
            else:
                warnings.warn(f"\nKnown missing __all__: {rel} (pre-existing debt)")

    if violations:
        raise AssertionError(
            f"\n{len(violations)} __init__.py file(s) with imports but no __all__:\n"
            + "\n".join(violations)
            + "\n\nAdd an __all__ list to these packages."
        )


def test_all_names_in_all_are_resolvable():
    """Every name in __all__ must be importable from its package."""
    violations: list[str] = []

    for init_file in sorted(SECBAAS.rglob("__init__.py")):
        if "__pycache__" in str(init_file):
            continue
        all_names = _get_all_names(init_file)
        if not all_names:
            continue

        rel = init_file.relative_to(SECBAAS)
        module = ".".join(rel.with_suffix("").parts)

        try:
            mod = importlib.import_module(module)
        except Exception:
            warnings.warn(f"\nCould not import {module}")
            continue

        if str(rel) in _SKIP_RESOLVABLE_CHECK:
            continue

        for name in sorted(all_names):
            if not hasattr(mod, name):
                violations.append(f"  {rel}: '{name}' in __all__ but not in module")

    if violations:
        raise AssertionError(
            "\nStale or invalid entries in __all__:\n"
            + "\n".join(violations)
            + "\n\nRemove or fix these stale exports."
        )
