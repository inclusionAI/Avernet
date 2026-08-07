"""Architecture enforcement: core→env-access regression tracker.

Derived from the Microkernel Architecture Constitution (Rule 14 watchdog):

Core logic must NOT access environment variables (``os.environ``, ``os.getenv``)
directly.  Environment-specific branching and implementation selection
must occur only in composition roots (``bootstrap/``, ``config/``).

Unlike BAAS (which had an ``env_utils`` import problem), Gateway has no
env_utils utility module.  The rule here is simpler and stricter: **core**
files must not contain raw ``os.environ`` / ``os.getenv`` calls.  Gateway
core receives all configuration via the DI container (``bootstrap/``) and
the ``config/`` layer.

Audit date: 2026-08-04 — baseline is zero (no violations).
"""

import ast
import re
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# ── Known baseline (Rule 14 debt) ────────────────────────────────────────
# Gateway core has zero direct os.environ / os.getenv calls.  All env
# access is gated through the config/ layer (ConfigLoader) and the
# bootstrap/ DI container.
#
# Audit date: 2026-08-04

_KNOWN_CORE = {
    "module_level_files": 0,
    "lazy_files": 0,
    "total_files": 0,
}

_ENV_PATTERN = re.compile(r"(\bos\.(?:environ|getenv)\b)")


def _scan_env_access(
    scan_dir: Path,
) -> dict[str, list[tuple[str, int, str, bool]]]:
    """Scan *scan_dir* for ``os.environ`` / ``os.getenv`` calls.

    Uses regex on source lines for detection (simpler and more robust than
    AST-walking for this pattern-matching task).
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
        rel_path = py_file.relative_to(GATEWAY)
        layer = rel_path.parts[0]

        for lineno, line in enumerate(source_lines, start=1):
            match = _ENV_PATTERN.search(line)
            if not match:
                continue
            # Skip comment lines and string-only lines
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith(
                ("'''", '"""', "r'''", 'r"""')
            ):
                continue

            is_lazy = _line_is_inside_body(source_lines, lineno)
            results.setdefault(layer, []).append(
                (str(rel_path), lineno, match.group(1), is_lazy)
            )

    return results


def _line_is_inside_body(source_lines: list[str], lineno: int) -> bool:
    """Check if a line is inside a function/method/class body."""
    line = source_lines[lineno - 1]
    if not line.startswith(("    ", "\t")):
        return False
    for prior in source_lines[: lineno - 1]:
        stripped = prior.strip()
        if stripped.startswith(("def ", "async def ", "class ")):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_core_env_access_count_does_not_regress():
    """Rule 14 watchdog: Core env-access count must not increase.

    Fails if new core files start calling ``os.environ`` or ``os.getenv``
    directly.  Passes (by updating ``_KNOWN_CORE``) when debt is paid down.
    """
    access_results = _scan_env_access(GATEWAY)
    core_access = access_results.get("core", [])

    module_level = sum(1 for _, _, _, lazy in core_access if not lazy)
    lazy_count = sum(1 for _, _, _, lazy in core_access if lazy)

    failures: list[str] = []

    if module_level > _KNOWN_CORE["module_level_files"]:
        failures.append(
            f"module-level env access: {module_level} "
            f"(known: {_KNOWN_CORE['module_level_files']})"
        )
    if lazy_count > _KNOWN_CORE["lazy_files"]:
        failures.append(
            f"lazy env access: {lazy_count} (known: {_KNOWN_CORE['lazy_files']})"
        )

    if failures:
        detail_lines = []
        for rel_path, lineno, text, lazy in core_access:
            tag = " [lazy]" if lazy else ""
            detail_lines.append(f"  {rel_path}:{lineno}: {text}{tag}")
        raise AssertionError(
            "\nCore env-access count exceeded known baseline:\n"
            + "\n".join(failures)
            + "\n\nCurrent violations:\n"
            + "\n".join(detail_lines)
            + "\n\nCore must not access os.environ/os.getenv directly. "
            "Inject configuration via the DI container (bootstrap/). "
            "Update _KNOWN_CORE constants when debt is paid down."
        )


def test_core_env_access_debt_paydown_detected():
    """Rule 14 watchdog: Warn when env-access count decreases.

    This test PASSES silently if count matches known baseline.
    It WARNS when count drops below baseline (debt is being paid down).
    """
    access_results = _scan_env_access(GATEWAY)
    core_access = access_results.get("core", [])

    total = len(core_access)

    if total < _KNOWN_CORE["total_files"]:
        import warnings

        warnings.warn(
            f"\nCore env-access debt decreased! "
            f"Now {total} files (was {_KNOWN_CORE['total_files']}). "
            f"Update _KNOWN_CORE constants in {__file__}."
        )


def test_core_env_access_sources_identifiable():
    """Rule 14 watchdog: Report env-access details for audit trail."""
    access_results = _scan_env_access(GATEWAY)
    core_access = access_results.get("core", [])

    module_level: list[str] = []
    lazy_accesses: list[str] = []

    for rel_path, lineno, text, lazy in core_access:
        entry = f"  {rel_path}:{lineno}: {text}"
        if lazy:
            lazy_accesses.append(entry)
        else:
            module_level.append(entry)

    import warnings

    warnings.warn(
        f"\nCore env-access audit (baseline: {_KNOWN_CORE['total_files']} files):\n"
        f"  Module-level ({len(module_level)}):\n"
        + ("\n".join(sorted(module_level)) if module_level else "    (none)")
        + f"\n  Lazy/function-body ({len(lazy_accesses)}):\n"
        + ("\n".join(sorted(lazy_accesses)) if lazy_accesses else "    (none)")
    )


def test_non_core_env_accessors_documented():
    """Rule 14: Document non-core env-accessors as expected.

    These files are in layers that legitimately access environment variables:
    config/, plugins/, bootstrap/, adapters/, tracer/.  This test simply
    documents them — no failure.
    """
    access_results = _scan_env_access(GATEWAY)

    allowed_layers = {
        "adapters",
        "bootstrap",
        "config",
        "logger",
        "plugins",
        "tracer",
    }
    documented: list[str] = []

    for layer in sorted(access_results):
        if layer in allowed_layers:
            for rel_path, lineno, text, lazy in access_results[layer]:
                tag = " [lazy]" if lazy else ""
                documented.append(f"  {rel_path}:{lineno}: {text}{tag}")

    if documented:
        import warnings

        warnings.warn(
            f"\nNon-core env-accessors ({len(documented)} sites) — expected "
            f"in config/plugins/bootstrap:\n" + "\n".join(sorted(documented))
        )
