"""Architecture enforcement: function boundaries and file sizing (Rule 9 — Guideline).

Derived from the Microkernel Architecture Constitution:

- **Rule 9** — Files and functions should serve single purposes.  Oversized source
  files (>500 lines) and cross-layer architectural imports signal degraded
  boundaries.  This test emits *warnings* only (never FAILs) because Rule 9
  is a Guideline.

Checks performed:
  1. Project-wide file size — flag any ``.py`` file under ``secbaas/`` exceeding
     500 lines, with a softer threshold for protocol/model files (1000 lines).
  2. Cross-layer imports — flag adapter files that import from BOTH
     ``community.api`` and ``community.core`` (concern mixing).
  3. Fat functions — flag any function/method in source files longer than 50
     lines (suggesting it does too much in a single function).

KNOWN PRE-EXISTING OVERSIZE (documented for comparison):
  Top 5 largest files:
  - ``core/service/publish_manage/_publish_service.py``: 4379 lines
  - ``core/service/device_manage/_device_service.py``: 2326 lines
  - ``core/service/paas/_local_paas_service.py``: 2014 lines
  - ``core/service/paas/_facade.py``: 1783 lines
  - ``core/repository/device_binding/_orm_repository.py``: 1425 lines
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"
_ADAPTERS_DIR = SECBAAS / "adapters" / "web"

# File size threshold (lines) — applies to ALL source files under secbaas/
_FILE_SIZE_THRESHOLD = 500

# Higher threshold for protocol/model/init files (typically more declarative)
_HIGH_THRESHOLD_KINDS = {"_protocols.py", "_models.py", "__init__.py"}

# Layer groupings for cross-layer import detection
_CONTRACT_LAYERS = {
    "secbaas.community.community.api",
    "secbaas.community.community.spi",
}
_CORE_LAYERS = {"secbaas.community.community.core", "secbaas.community.domain"}
_PLUGIN_LAYERS = {"secbaas.community.community.plugins"}
_ADAPTER_LAYERS = {"secbaas.community.community.adapters"}

# Function body line threshold for "fat function" detection
_FAT_FUNCTION_THRESHOLD = 50


# ═══════════════════════════════════════════════════════════════════════════
# Rule 9: Project-wide file size
# ═══════════════════════════════════════════════════════════════════════════


def test_source_files_not_oversized():
    """Rule 9 (Guideline): Source files should stay focused.

    Flags any ``.py`` file under ``secbaas/`` exceeding 500 lines
    (or 1000 lines for protocol/model/init files).  Known oversized files
    are included for comparison.

    Emits ``warnings.warn()`` only — never FAILs.
    """
    violations: list[tuple[str, int]] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            line_count = len(py_file.read_text().splitlines())
        except OSError:
            continue

        kind = py_file.name
        if kind in _HIGH_THRESHOLD_KINDS and line_count <= 1000:
            continue

        if line_count > _FILE_SIZE_THRESHOLD:
            rel = py_file.relative_to(SECBAAS).as_posix()
            violations.append((rel, line_count))

    if not violations:
        return

    # Sort by line count descending, show top 30
    violations.sort(key=lambda x: -x[1])
    lines = [f"  {rel}: {count} lines" for rel, count in violations[:30]]
    if len(violations) > 30:
        lines.append(f"  ... and {len(violations) - 30} more files")

    warnings.warn(
        f"\n{len(violations)} source file(s) exceeding {_FILE_SIZE_THRESHOLD} lines "
        f"(Rule 9 Guideline — consider splitting):\n" + "\n".join(lines)
    )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 9: Cross-layer import detection
# ═══════════════════════════════════════════════════════════════════════════


def _imported_layers(tree: ast.AST) -> set[str]:
    """Extract the set of secbaas.* top-level layers imported by an AST tree.

    For ``from community.X.Y import ...`` we record ``community.X``.
    For ``import community.X.Y`` we record ``community.X``.
    """
    layers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.startswith("secbaas.community."):
                parts = node.module.split(".")
                if len(parts) >= 2:
                    layers.add(f"{parts[0]}.{parts[1]}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("secbaas.community."):
                    parts = alias.name.split(".")
                    if len(parts) >= 2:
                        layers.add(f"{parts[0]}.{parts[1]}")
    return layers


def test_no_mixed_architectural_imports():
    """Rule 9 (Guideline): A single adapter file should not mix contract and core imports.

    If a file imports from BOTH ``community.api`` AND ``community.core``, it
    signals mixed architectural concerns — the api layer is the boundary,
    and importing core directly bypasses it.

    Emits ``warnings.warn()`` for any file with cross-layer mixing.
    Never FAILs — this is a guideline, not a hard rule.
    """
    violations: list[str] = []

    for py_file in sorted(_ADAPTERS_DIR.rglob("*.py")):
        if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        layers = _imported_layers(tree)

        has_api = bool(layers & _CONTRACT_LAYERS)
        has_core = bool(layers & _CORE_LAYERS)

        if has_api and has_core:
            rel = py_file.relative_to(SECBAAS).as_posix()
            api_layers = sorted(
                set(mod.split(".")[1] for mod in (layers & _CONTRACT_LAYERS))
            )
            core_layers = sorted(
                set(mod.split(".")[1] for mod in (layers & _CORE_LAYERS))
            )
            violations.append(f"  {rel}: api={api_layers}, core={core_layers}")

    if not violations:
        return

    warnings.warn(
        f"\n{len(violations)} adapter file(s) with mixed contract+core imports "
        f"(Rule 9 Guideline — consider routing through api layer only):\n"
        + "\n".join(sorted(violations))
    )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 9: Fat function detection across all source files
# ═══════════════════════════════════════════════════════════════════════════


def test_no_fat_functions():
    f"""Rule 9 (Guideline): Functions should stay focused.

    Flags any function/method across all ``secbaas/`` source files whose
    body exceeds {_FAT_FUNCTION_THRESHOLD} lines.  Long functions often signal mixed
    concerns and should be decomposed.

    Emits ``warnings.warn()`` only — never FAILs.
    """

    violations: list[tuple[str, str, int]] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            if node.body:
                start = node.body[0].lineno
                end = node.body[-1].end_lineno or start
                body_size = (end - start) + 1
            else:
                body_size = 0

            if body_size > _FAT_FUNCTION_THRESHOLD:
                rel = py_file.relative_to(SECBAAS).as_posix()
                violations.append((rel, node.name, body_size))

    if not violations:
        return

    lines = []
    for rel, func_name, size in sorted(violations, key=lambda x: (-x[2], x[0], x[1])):
        lines.append(f"  {rel} :: {func_name}() — {size} line body")

    warnings.warn(
        f"\n{len(violations)} fat function(s) across all source files exceeding "
        f"{_FAT_FUNCTION_THRESHOLD} lines "
        f"(Rule 9 Guideline — extract into smaller helpers/services):\n"
        + "\n".join(lines)
    )
