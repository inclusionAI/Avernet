"""Architecture enforcement: protocol export rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 12** — Protocol classes defined in ``spi/*/_protocols.py`` must
  be properly exported via ``__all__`` in their parent package's
  ``__init__.py``.
"""

import ast
import importlib
from collections.abc import Generator
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# Protocol classes exempted from __all__ checks
_SPI_EXEMPT_CLASSES: dict[str, set[str]] = {}


def _protocol_classes_in_file(filepath: Path) -> Generator[str, None, None]:
    """Yield Protocol class names from a ``_protocols.py`` file (AST-based)."""
    if not filepath.exists():
        return
    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        ):
            yield node.name


def _extract_all_names(init_path: Path) -> list[str] | None:
    """Extract ``__all__`` list from a package ``__init__.py`` (AST-based)."""
    if not init_path.exists():
        return None
    try:
        tree = ast.parse(init_path.read_text())
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
            and isinstance(node.value, ast.List)
        ):
            return [
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    return None


def test_spi_protocol_classes_are_in_all() -> None:
    """Rule 12: Every Protocol class in ``spi/*/_protocols.py`` must appear
    in its parent package's ``__all__`` list.
    """
    proto_root = GATEWAY / "spi"
    missing: dict[str, list[str]] = {}

    for proto_file in sorted(proto_root.rglob("_protocols.py")):
        pkg_rel = str(proto_file.parent.relative_to(GATEWAY))
        init_file = proto_file.parent / "__init__.py"
        all_names = _extract_all_names(init_file)

        if all_names is None:
            continue  # no __all__ → skip (not a public package)

        exempt = _SPI_EXEMPT_CLASSES.get(pkg_rel, set())
        file_rel = str(proto_file.relative_to(GATEWAY))

        for class_name in _protocol_classes_in_file(proto_file):
            if class_name in exempt:
                continue
            if class_name not in all_names:
                missing.setdefault(file_rel, []).append(class_name)

    if missing:
        lines = []
        for file_rel, classes in sorted(missing.items()):
            lines.append(f"  {file_rel}:")
            for c in classes:
                lines.append(f"    - {c}")
        raise AssertionError(
            "\nSPI Protocol class(es) missing from __all__:\n" + "\n".join(lines)
        )


def test_all_entries_in_all_are_resolvable() -> None:
    """Rule 12b: Every name listed in ``__all__`` in ``spi/`` package
    ``__init__.py`` files must be resolvable via ``importlib``.
    """
    unresolvable: dict[str, list[str]] = {}
    root = GATEWAY / "spi"

    for init_file in root.rglob("__init__.py"):
        all_names = _extract_all_names(init_file)
        if all_names is None:
            continue

        rel = init_file.parent.relative_to(GATEWAY)
        package = f"gateway.community.{str(rel).replace('/', '.')}"

        try:
            mod = importlib.import_module(package)
        except Exception as exc:
            unresolvable.setdefault(str(rel), []).append(
                f"(cannot import {package}: {exc})"
            )
            continue

        for name in all_names:
            try:
                getattr(mod, name)
            except AttributeError:
                file_rel = str(rel)
                unresolvable.setdefault(file_rel, []).append(name)

    if unresolvable:
        lines = []
        for file_rel, names in sorted(unresolvable.items()):
            lines.append(f"  {file_rel}:")
            for n in names:
                lines.append(f"    - {n}")
        raise AssertionError(
            "\nUnresolvable __all__ entry/entries:\n" + "\n".join(lines)
        )
