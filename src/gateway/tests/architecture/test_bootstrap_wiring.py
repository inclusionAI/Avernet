"""Architecture enforcement: bootstrap wiring rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 14** — Configuration drives all wiring; environment-specific
  branching must occur only in composition roots (``bootstrap/``,
  ``adapters/``, ``config/``, ``plugins/``).  Direct calls to
  ``os.environ`` / ``os.getenv`` in ``core``, ``api``, or ``spi`` layers
  are policy violations because environment decisions should remain
  injectable rather than runtime-detected.

This test is a POLICY DOCUMENTATION tool: it always warns, never fails.
"""

import ast
import warnings
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# Layers where direct environment access is expected / acceptable.
# - bootstrap/  — composition root (assembles containers)
# - adapters/   — HTTP transport layer (FastAPI wiring)
# - config/     — configuration loading (reads env for file paths, overlays)
# - plugins/    — plugin implementations (are wiring/selection points)
ALLOWED_LAYERS = {"bootstrap", "adapters", "config", "plugins"}

# Layers that should NOT contain direct env access.
# These are architectural violations that need to be migrated into
# bootstrap configuration injection.
WARN_LAYERS = {"core", "api", "spi"}

# Environment-access call patterns we track at the AST level.
# Detects: os.environ[...], os.environ.get(...), os.getenv(...)
_ENV_ACCESS_MODULE = "os"

# Attribute-qualified patterns:
#   os.environ.get(...)  →  attr="get",  value_attr="environ"
#   os.getenv(...)       →  attr="getenv", no value_attr check
_ENV_ACCESS_ATTRS = frozenset({"getenv", "environ"})


def _find_env_access_calls(
    scan_dir: Path,
) -> dict[str, list[tuple[str, int, str]]]:
    """Scan *scan_dir* for direct environment-access call-sites.

    Returns dict mapping layer name (e.g. ``'core'``, ``'adapters'``) to
    a list of ``(rel_path, lineno, call_text)`` tuples.

    Detects these call patterns:

    * ``os.environ.get("VAR")`` — qualified call through ``os.environ.get``.
    * ``os.environ["VAR"]`` — subscript access on ``os.environ``.
    * ``os.getenv("VAR")`` — direct ``os.getenv`` call.

    ``__pycache__`` directories are skipped.
    """
    results: dict[str, list[tuple[str, int, str]]] = {}

    for py_file in sorted(scan_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel_path = py_file.relative_to(GATEWAY)
        # Files at the community package root (e.g. plugin_accessor.py,
        # main.py) have no layer subdirectory — skip them as they are
        # bootstrap-adjacent wiring files.
        layer = rel_path.parts[0]
        if layer not in ALLOWED_LAYERS and layer not in WARN_LAYERS:
            continue

        for node in ast.walk(tree):
            call_text: str | None = None

            # Pattern 1: os.environ.get("VAR") or os.getenv("VAR")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                func = node.func
                # os.getenv(...)
                if (
                    func.attr in ("getenv",)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == _ENV_ACCESS_MODULE
                ):
                    call_text = f"os.{func.attr}"
                # os.environ.get(...)
                elif (
                    func.attr in ("get",)
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "environ"
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == _ENV_ACCESS_MODULE
                ):
                    call_text = "os.environ.get"

            # Pattern 2: os.environ["VAR"] — subscript access
            if (
                call_text is None
                and isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == _ENV_ACCESS_MODULE
            ):
                call_text = "os.environ[...]"

            if call_text is not None:
                lineno = getattr(node, "lineno", 0)
                results.setdefault(layer, []).append((str(rel_path), lineno, call_text))

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_env_access_confined_to_allowed_layers():
    """Rule 14: env-access calls in core/api/spi should be injected via bootstrap.

    Scans all layers for ``os.environ`` / ``os.getenv`` call-sites.

    * Calls in ``ALLOWED_LAYERS`` are silently counted.
    * Calls in ``WARN_LAYERS`` (core, api, spi) emit a ``warnings.warn()``
      with the file path, line number, and call text.

    This test NEVER fails — it is a policy-documentation and migration-guide
    tool, not an invariant check.
    """
    call_results = _find_env_access_calls(GATEWAY)

    allowed_count = 0
    warn_items: list[str] = []

    for layer in sorted(call_results):
        for rel_path, lineno, call_text in call_results[layer]:
            if layer in ALLOWED_LAYERS:
                allowed_count += 1
            elif layer in WARN_LAYERS:
                warn_items.append(f"  {layer:>9s}  {rel_path}:{lineno}  {call_text}")

    if warn_items:
        warnings.warn(
            f"\nRule 14: {len(warn_items)} env-access call-site(s) found in "
            f"non-wiring layers (core/api/spi).\n"
            f"These calls should be injected via bootstrap configuration "
            f"rather than detected at runtime.\n\n"
            f"Violations:\n"
            + "\n".join(sorted(warn_items))
            + "\n\n(See RULES-MANIFEST.md for the Rule 14 waiver log.)"
        )


def test_no_env_access_in_non_allowed_layers():
    """Rule 14 watchdog: env-access call-sites must not proliferate.

    Counts total env-access call-sites in ``WARN_LAYERS`` (core, api, spi).
    If the count exceeds zero, a warning is emitted with the current
    call-sites listed for the audit trail.

    This is a soft check — always warns, never fails.
    """
    call_results = _find_env_access_calls(GATEWAY)

    total_warn = 0
    for layer in WARN_LAYERS:
        total_warn += len(call_results.get(layer, []))

    # Document every call-site for audit trail visibility.
    if total_warn > 0:
        detail_lines: list[str] = []
        for layer in sorted(WARN_LAYERS & set(call_results)):
            for rel_path, lineno, call_text in call_results[layer]:
                detail_lines.append(f"  {layer:>9s}  {rel_path}:{lineno}  {call_text}")

        warnings.warn(
            f"\nRule 14 watchdog: {total_warn} env-access call-site(s) "
            f"remain in WARN_LAYERS (core/api/spi).\n"
            f"Baseline target: 0 (all env branching in "
            f"bootstrap/adapters/config/plugins).\n\n"
            f"Current call-sites:\n"
            + "\n".join(sorted(detail_lines))
            + "\n\nThese should be refactored to receive environment config "
            "via bootstrap injection rather than calling os.environ/getenv "
            "directly. Update RULES-MANIFEST.md when debt is paid down."
        )
