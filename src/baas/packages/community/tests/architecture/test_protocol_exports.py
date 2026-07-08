"""Architecture enforcement: protocol export rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 12** — Protocol classes defined in ``api/*/_protocols.py`` and
  ``spi/*/_protocols.py`` must be properly exported via ``__all__`` in their
  parent package's ``__init__.py``.

  This test verifies that:
  1. Every Protocol class in ``api/`` is listed in the package's ``__all__``.
  2. Every Protocol class in ``spi/`` is listed in the package's ``__all__``
     (sandbox sub-protocols and ``ConnectionProvider`` are exempted).
  3. Every name listed in ``__all__`` files is actually resolvable via
     ``from <package> import <name>``.
"""

import ast
import importlib
import warnings
from collections.abc import Generator
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

# ── Protocol classes exempted from __all__ checks ────────────────────────
# ConnectionProvider is a sub-contract used internally by DataSourcePlugin.
_SPI_EXEMPT_CLASSES: dict[str, set[str]] = {
    "spi/database": {"ConnectionProvider"},
}

# ── Sandbox sub-packages (warnings instead of failures) ──────────────────
_SANDBOX_SUB_PACKAGES = {"arca", "desktop", "docker", "k8s", "poolab"}


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
    """Extract ``__all__`` list from a package ``__init__.py`` (AST-based).

    Returns ``None`` if ``__all__`` is not found.
    """
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


def _relative_package(proto_file: Path, base_dir: str) -> str:
    """Return the dot-separated package path relative to SECBAAS.

    For ``spi/database/_protocols.py`` → ``spi.database``
    """
    rel = proto_file.parent.relative_to(SECBAAS)
    return str(rel).replace("/", ".")


# ═══════════════════════════════════════════════════════════════════════════
# Rule 12: Protocol classes must be in __all__
# ═══════════════════════════════════════════════════════════════════════════


def _collect_missing(
    base_dir_name: str,
    exempt_classes: dict[str, set[str]],
    sandbox_sub_packages: set[str],
) -> dict[str, list[str]]:
    """Collect Protocol classes not listed in ``__all__``.

    Returns: ``{proto_file_rel: [missing_class_names]}``
    """
    proto_root = SECBAAS / base_dir_name
    missing: dict[str, list[str]] = {}

    for proto_file in sorted(proto_root.rglob("_protocols.py")):
        pkg_rel = str(proto_file.parent.relative_to(SECBAAS))
        init_file = proto_file.parent / "__init__.py"
        all_names = _extract_all_names(init_file)

        if all_names is None:
            continue  # no __all__ → skip (not a public package)

        exempt = exempt_classes.get(pkg_rel, set())
        file_rel = str(proto_file.relative_to(SECBAAS))

        for class_name in _protocol_classes_in_file(proto_file):
            if class_name in exempt:
                continue
            if class_name not in all_names:
                missing.setdefault(file_rel, []).append(class_name)

    return missing


def test_api_protocol_classes_are_in_all():
    """Rule 12: Every Protocol class in ``api/*/_protocols.py`` must appear
    in its parent package's ``__all__`` list.
    """
    missing = _collect_missing("api", {}, set())

    if missing:
        lines = []
        for file_rel, classes in sorted(missing.items()):
            lines.append(f"  {file_rel}:")
            for c in classes:
                lines.append(f"    - {c}")
        raise AssertionError(
            "\nAPI Protocol class(es) missing from __all__:\n" + "\n".join(lines)
        )


def test_spi_protocol_classes_are_in_all():
    """Rule 12: Every Protocol class in ``spi/*/_protocols.py`` must appear
    in its parent package's ``__all__`` list.

    Exemptions:
      - ``ConnectionProvider`` in ``spi/database/_protocols.py`` (sub-contract)
      - Sandbox sub-protocols emit *warnings* instead of failures (they are
        re-exported from the ``spi/sandbox/__init__.py`` aggregator).
    """
    missing = _collect_missing("spi", _SPI_EXEMPT_CLASSES, _SANDBOX_SUB_PACKAGES)

    # Separate sandbox warnings from hard failures
    sandbox_missing: dict[str, list[str]] = {}
    hard_missing: dict[str, list[str]] = {}

    for file_rel, classes in sorted(missing.items()):
        # Check if this is a sandbox sub-protocol
        parts = file_rel.split("/")
        if len(parts) >= 3 and parts[0] == "spi" and parts[1] == "sandbox":
            # sub-package like spi/sandbox/arca/_protocols.py
            sub = parts[2] if len(parts) > 2 else ""
            if sub in _SANDBOX_SUB_PACKAGES:
                sandbox_missing[file_rel] = classes
                continue
        hard_missing[file_rel] = classes

    # Warn for sandbox sub-protocols
    if sandbox_missing:
        lines = []
        for file_rel, classes in sorted(sandbox_missing.items()):
            lines.append(f"  {file_rel}:")
            for c in classes:
                lines.append(f"    - {c} (re-exported via spi/sandbox/__init__.py)")
        warnings.warn(
            "\nSandbox sub-protocol(s) not in sub-package __all__:\n" + "\n".join(lines)
        )

    # Hard-fail for everything else
    if hard_missing:
        lines = []
        for file_rel, classes in sorted(hard_missing.items()):
            lines.append(f"  {file_rel}:")
            for c in classes:
                lines.append(f"    - {c}")
        raise AssertionError(
            "\nSPI Protocol class(es) missing from __all__:\n" + "\n".join(lines)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 12b: Every __all__ entry must be resolvable
# ═══════════════════════════════════════════════════════════════════════════


def _all_init_files(base_dir: str) -> list[Path]:
    """Yield all ``__init__.py`` files under ``base_dir`` that define ``__all__``."""
    init_files: list[Path] = []
    root = SECBAAS / base_dir
    for init_file in root.rglob("__init__.py"):
        if _extract_all_names(init_file) is not None:
            init_files.append(init_file)
    return init_files


def test_all_entries_in_all_are_resolvable():
    """Rule 12b: Every name listed in ``__all__`` in ``api/`` and ``spi/``
    package ``__init__.py`` files must be resolvable via ``importlib``.
    """
    unresolvable: dict[str, list[str]] = {}

    for base_dir in ("api", "spi"):
        for init_file in _all_init_files(base_dir):
            # Build the absolute import path
            rel = init_file.parent.relative_to(SECBAAS)
            package = f"secbaas.{str(rel).replace('/', '.')}"
            all_names = _extract_all_names(init_file)
            if all_names is None:
                continue

            try:
                mod = importlib.import_module(package)
            except Exception as exc:
                unresolvable.setdefault(str(init_file.relative_to(SECBAAS)), []).append(
                    f"(cannot import {package}: {exc})"
                )
                continue

            for name in all_names:
                try:
                    getattr(mod, name)
                except AttributeError:
                    file_rel = str(init_file.relative_to(SECBAAS))
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
