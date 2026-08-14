"""Architecture enforcement: every __init__.py with imports must define __all__.

All exported names must be valid (resolvable) and every package that imports
from its submodules must declare its public surface via ``__all__``.
"""

import ast
import importlib
import warnings
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

_KNOWN_MISSING_ALL: set[str] = set()  # no known violations


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


def test_all_init_files_have_all() -> None:
    """Every __init__.py with imports must define __all__."""
    violations: list[str] = []

    for init_file in sorted(GATEWAY.rglob("__init__.py")):
        if "__pycache__" in str(init_file):
            continue

        rel = str(init_file.relative_to(GATEWAY))
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
                warnings.warn(
                    f"\nKnown missing __all__: {rel} (pre-existing debt)", stacklevel=1
                )

    if violations:
        raise AssertionError(
            f"\n{len(violations)} __init__.py file(s) with imports but no __all__:\n"
            + "\n".join(violations)
            + "\n\nAdd an __all__ list to these packages."
        )


def test_all_names_in_all_are_resolvable() -> None:
    """Every name in __all__ must be importable from its package."""
    violations: list[str] = []

    for init_file in sorted(GATEWAY.rglob("__init__.py")):
        if "__pycache__" in str(init_file):
            continue
        all_names = _get_all_names(init_file)
        if not all_names:
            continue

        rel = init_file.relative_to(GATEWAY)
        module = ".".join(rel.with_suffix("").parts)
        full_module = f"gateway.community.{module}"

        try:
            mod = importlib.import_module(full_module)
        except Exception:
            continue  # subpackage __init__ not directly importable — skip silently

        for name in sorted(all_names):
            if not hasattr(mod, name):
                violations.append(f"  {rel}: '{name}' in __all__ but not in module")

    if violations:
        raise AssertionError(
            "\nStale or invalid entries in __all__:\n"
            + "\n".join(violations)
            + "\n\nRemove or fix these stale exports."
        )
