"""Architecture enforcement: adapter thinness rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 7** — Adapters must remain thin.  The delivery layer (adapters/web/)
  should only translate between API protocols and domain operations.  Domain
  logic, orchestration, and concurrency management belong in the core layer.

  This test enforces adapter thinness by scanning for:
  1. Concurrency patterns (``asyncio.create_task``, ``asyncio.wait_for``,
     ``asyncio.gather``) — orchestration belongs in core services.
  2. Duplicate function definitions across adapter files — extract into
     a shared utility if needed in multiple routes.
  3. Domain imports — adapters must not import from ``community.core``
     at module level.
"""

import ast
import warnings
from pathlib import Path

from pytestarch import Rule

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"
_ADAPTERS_DIR = _SOURCE_ROOT / "adapters" / "web"

# ── Known pre-existing violations ──────────────────────────────────────────
# Excluded from test FAILURE; emit WARNING only.
_KNOWN_THINNESS_VIOLATIONS: set[str] = {
    "_relay_ws.py",  # asyncio.create_task + gather for WS relay management
}
"""Pre-existing adapter thinness debt — warn only, don't fail."""

_KNOWN_DUPES: set[str] = {"_bundle", "_error"}
"""Pre-existing private helper name collisions across adapter files."""


# ═══════════════════════════════════════════════════════════════════════════
# ── Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _relative_path(adapter_dir: Path, py_file: Path) -> str:
    """Return path relative to adapter_dir for consistent lookups."""
    return str(py_file.relative_to(adapter_dir))


def _scan_for_domain_patterns(adapter_dir: Path) -> dict[str, list[str]]:
    """Scan all ``.py`` files under *adapter_dir* for AST patterns that
    indicate domain logic has leaked into the adapter layer.

    Returns a dict mapping file relative-path to a list of violation messages.
    """
    violations: dict[str, list[str]] = {}

    # ── Concurrency patterns ───────────────────────────────────────────
    _CONCURRENCY_FUNCS = {
        "asyncio.create_task",
        "asyncio.wait_for",
        "asyncio.gather",
    }

    for py_file in sorted(adapter_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        rel = _relative_path(adapter_dir, py_file)

        # Concurrency patterns — AST-based function call check
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Check for asyncio.xxx() — ast.Attribute with value=asyncio
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                full_name = f"asyncio.{node.func.attr}"
                if full_name in _CONCURRENCY_FUNCS:
                    violations.setdefault(rel, []).append(
                        f"  L{node.lineno}: {full_name}() — concurrency "
                        f"orchestration belongs in core services, not adapters"
                    )

    return violations


# ═══════════════════════════════════════════════════════════════════════════
# Test: no asyncio orchestration in adapters
# ═══════════════════════════════════════════════════════════════════════════


def test_no_new_asyncio_orchestration_in_adapters():
    """Rule 7: Adapters must not use ``asyncio.create_task``,
    ``asyncio.wait_for``, or ``asyncio.gather``.

    Concurrency orchestration belongs in the core layer.
    Pre-existing violations in known files emit a warning; new
    violations fail the build.
    """
    violations = _scan_for_domain_patterns(_ADAPTERS_DIR)

    # Filter to only concurrency-related violations
    asyncio_violations: dict[str, list[str]] = {
        f: msgs for f, msgs in violations.items() if any("asyncio." in m for m in msgs)
    }

    known: list[str] = []
    new: list[str] = []

    for fpath, msgs in sorted(asyncio_violations.items()):
        if fpath in _KNOWN_THINNESS_VIOLATIONS:
            known.append(f"{fpath}:\n" + "\n".join(msgs))
        else:
            new.append(f"{fpath}:\n" + "\n".join(msgs))

    if known:
        warnings.warn(
            f"\nKnown asyncio orchestration in {len(known)} adapter file(s) "
            f"(pre-existing debt):\n" + "\n".join(known)
        )

    if new:
        raise AssertionError(
            f"\n{len(new)} adapter file(s) with NEW asyncio orchestration:\n"
            + "\n".join(new)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test: no duplicate function definitions across adapter files
# ═══════════════════════════════════════════════════════════════════════════


def test_no_duplicate_function_definitions():
    """Rule 7: Adapter files must not define identically-named functions.

    Duplicate private helpers across router files indicate an opportunity
    to extract shared utility code.
    """
    file_funcs: dict[str, set[str]] = {}

    for py_file in sorted(_ADAPTERS_DIR.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel = _relative_path(_ADAPTERS_DIR, py_file)
        func_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name != "__init__":
                    func_names.add(node.name)
        if func_names:
            file_funcs[rel] = func_names

    # Find private functions (starting with _) that appear in 2+ files.
    # Public function name collisions across router files are expected
    # (e.g., get_device_info in different routers).
    # Only flag private helper duplication.
    func_to_files: dict[str, set[str]] = {}
    for fpath, names in file_funcs.items():
        for name in names:
            if name.startswith("_"):
                func_to_files.setdefault(name, set()).add(fpath)

    duplicates = {
        name: files
        for name, files in func_to_files.items()
        if len(files) > 1 and name not in _KNOWN_DUPES
    }

    if duplicates:
        new: list[str] = []
        for name, files in sorted(duplicates.items()):
            new.append(f"  {name}: {', '.join(sorted(files))}")

        if new:
            raise AssertionError(
                "\nDuplicate private function definition(s) across adapter files:\n"
                + "\n".join(new)
            )


# ═══════════════════════════════════════════════════════════════════════════
# Test: adapters must not import from domain layers
# ═══════════════════════════════════════════════════════════════════════════


def test_no_domain_imports_in_adapters(project_architecture):
    """Rule 7: Adapters must not import from ``community.core``.

    Adapters should depend only on SPI/protocol interfaces, not on
    concrete core domain modules.  (Uses pytestarch module-level import
    analysis.)
    """
    rule = (
        Rule()
        .modules_that()
        .are_sub_modules_of("gateway.community.adapters.web")
        .should_not()
        .import_modules_that()
        .are_sub_modules_of("gateway.community.core")
    )
    rule.assert_applies(project_architecture)
