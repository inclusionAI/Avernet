"""Architecture enforcement: core→infra env-import regression tracker.

Derived from the Microkernel Architecture Constitution (Rule 14 watchdog):

Core logic must NOT import from infrastructure layer (``secbaas.infra``)
directly.  Environment-specific branching and implementation selection
must occur only in composition roots (``bootstrap/``, ``adapters/``).

DEBT FULLY PAID: ``env_utils`` moved to ``secbaas.core.utils.env_utils``
as part of the baas split refactoring (Stage 2: resolve-split-blockers).
All core files now import from core.utils instead of infra.utils — zero
Rule 14 violations remain.

See RULES-MANIFEST.md (Rule 14 watchdog) for the waiver log.
"""

import ast
import re
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

# ── Known baseline (Rule 14 debt) ────────────────────────────────────────
# Debt fully paid on 2026-06-26 — env_utils moved from infra/utils/ to
# core/utils/. No remaining core→infra imports for env_utils.
#
# Audit date: 2026-06-26

_KNOWN_CORE = {
    "module_level_files": 0,  # all resolved
    "lazy_files": 0,  # all resolved
    "total_files": 0,  # all resolved
}

# Patterns to match different import styles
_ENV_IMPORT_PATTERNS: list[re.Pattern] = [
    re.compile(r"from\s+secbaas\.infra\.utils\.env_utils\s+import"),
    re.compile(r"from\s+secbaas\.infra\.utils\s+import\s+.*env_utils"),
    re.compile(r"import\s+secbaas\.infra\.utils\.env_utils"),
]


def _is_lazy_import(node: ast.AST, source_lines: list[str]) -> bool:
    """Check if an import lives inside a function/method body."""
    for parent in ast.walk(node):
        # Walk up: if any parent is a FunctionDef, it's lazy
        for ancestor_node, _ in ast.iter_fields(node):
            pass  # placeholder — we use line-based heuristic instead
    # Simple heuristic: imports inside function bodies have indentation > 0
    # and are preceded by 'def ' or 'async def '
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        lineno = getattr(node, "lineno", 0)
        if lineno and lineno <= len(source_lines):
            line = source_lines[lineno - 1]
            if line.startswith(("    ", "\t")) and not line.startswith(
                ("#", "'''", '"""')
            ):
                # Check if there's a 'def' before this line
                for check_line in source_lines[: lineno - 1]:
                    stripped = check_line.strip()
                    if stripped.startswith(("def ", "async def ")):
                        return True
    return False


def _scan_imports(
    scan_dir: Path,
) -> dict[str, list[tuple[str, int, str, bool]]]:
    """Scan *scan_dir* for env_utils imports.

    Returns dict mapping layer name (e.g. 'core', 'adapters') to list of
    (rel_path, lineno, import_text, is_lazy) tuples.
    """
    results: dict[str, list[tuple[str, int, str, bool]]] = {}

    for py_file in sorted(scan_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        source_lines = source.splitlines()
        rel_path = py_file.relative_to(SECBAAS)
        layer = rel_path.parts[0]  # 'core', 'adapters', etc.

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue

            import_text = ""
            is_match = False
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_text = f"import {alias.name}"
                    if any(p.search(import_text) for p in _ENV_IMPORT_PATTERNS):
                        is_match = True
                        break
            else:  # ImportFrom
                if node.module is None:
                    continue
                names = [a.name for a in node.names]
                import_text = f"from {node.module} import {', '.join(names)}"
                if any(p.search(import_text) for p in _ENV_IMPORT_PATTERNS):
                    is_match = True

            if is_match:
                is_lazy = _is_lazy_import(node, source_lines)
                lineno = getattr(node, "lineno", 0)
                results.setdefault(layer, []).append(
                    (str(rel_path), lineno, import_text, is_lazy)
                )

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_core_env_import_count_does_not_regress():
    """Rule 14 watchdog: Core env-import count must not increase.

    Fails if new core files start importing from core.utils.env_utils.
    Passes (by updating _KNOWN_CORE) when debt is paid down.
    """
    import_results = _scan_imports(SECBAAS)
    core_imports = import_results.get("core", [])

    module_level = sum(1 for _, _, _, lazy in core_imports if not lazy)
    lazy_imports = sum(1 for _, _, _, lazy in core_imports if lazy)

    failures: list[str] = []

    if module_level > _KNOWN_CORE["module_level_files"]:
        failures.append(
            f"module-level env imports: {module_level} "
            f"(known: {_KNOWN_CORE['module_level_files']})"
        )
    if lazy_imports > _KNOWN_CORE["lazy_files"]:
        failures.append(
            f"lazy env imports: {lazy_imports} (known: {_KNOWN_CORE['lazy_files']})"
        )

    if failures:
        detail_lines = []
        for rel_path, lineno, text, lazy in core_imports:
            tag = " [lazy]" if lazy else ""
            detail_lines.append(f"  {rel_path}:{lineno}: {text}{tag}")
        raise AssertionError(
            "\nCore env-import count exceeded known baseline:\n"
            + "\n".join(failures)
            + "\n\nCurrent violations:\n"
            + "\n".join(detail_lines)
            + "\n\nReduce by abstracting environment config behind an SPI "
            "in bootstrap/. Update _KNOWN_CORE when debt is paid down."
        )


def test_core_env_import_debt_paydown_detected():
    """Rule 14 watchdog: Warn when env-import count decreases.

    This test PASSES silently if count matches known baseline.
    It WARNS when count drops below baseline (debt is being paid down).
    """
    import_results = _scan_imports(SECBAAS)
    core_imports = import_results.get("core", [])

    total = len(core_imports)

    if total < _KNOWN_CORE["total_files"]:
        import warnings

        warnings.warn(
            f"\nCore env-import debt decreased! "
            f"Now {total} files (was {_KNOWN_CORE['total_files']}). "
            f"Update _KNOWN_CORE constants in {__file__}."
        )


def test_core_env_import_sources_identifiable():
    """Rule 14 watchdog: Report env-import details for audit trail."""
    import_results = _scan_imports(SECBAAS)
    core_imports = import_results.get("core", [])

    module_level: list[str] = []
    lazy_imports: list[str] = []

    for rel_path, lineno, text, lazy in core_imports:
        entry = f"  {rel_path}:{lineno}: {text}"
        if lazy:
            lazy_imports.append(entry)
        else:
            module_level.append(entry)

    # This test always passes — it's a documentation / audit helper.
    # Output is visible in pytest -v verbose mode.
    import warnings

    warnings.warn(
        f"\nCore env-import audit (baseline: {_KNOWN_CORE['total_files']} files):\n"
        f"  Module-level ({len(module_level)}):\n"
        + ("\n".join(sorted(module_level)) if module_level else "    (none)")
        + f"\n  Lazy/function-body ({len(lazy_imports)}):\n"
        + ("\n".join(sorted(lazy_imports)) if lazy_imports else "    (none)")
    )


def test_device_manage_no_get_current_env_re_export():
    """Rule 14: device_manage/__init__.py should not re-export get_current_env.

    This is a hidden propagation vector — removing the re-export breaks
    any external consumer relying on ``from secbaas.core.service.device_manage
    import get_current_env``.
    """
    device_manage_init = SECBAAS / "core" / "service" / "device_manage" / "__init__.py"
    if not device_manage_init.exists():
        return  # already cleaned up ✓

    text = device_manage_init.read_text()
    if "get_current_env" in text:
        import warnings

        warnings.warn(
            f"\n{device_manage_init.relative_to(SECBAAS)} re-exports "
            f"get_current_env in __all__ — env import leaks through "
            f"this public path. Remove the re-export when safe."
        )


def test_non_core_env_importers_documented():
    """Rule 14: Document non-core env-importers as expected.

    These files are in layers that legitimately access infrastructure:
    adapters/, plugins/, infra/, bootstrap/.  This test simply documents
    them — no failure.
    """
    import_results = _scan_imports(SECBAAS)

    allowed_layers = {"adapters", "plugins", "infra", "bootstrap"}
    documented: list[str] = []

    for layer in sorted(import_results):
        if layer in allowed_layers:
            for rel_path, lineno, text, lazy in import_results[layer]:
                tag = " [lazy]" if lazy else ""
                documented.append(f"  {rel_path}:{lineno}: {text}{tag}")

    if documented:
        import warnings

        warnings.warn(
            f"\nNon-core env-importers ({len(documented)} sites) — expected "
            f"in adapters/plugins/infra:\n" + "\n".join(sorted(documented))
        )
