"""Architecture enforcement: bootstrap wiring rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 14** — Configuration drives all wiring; environment-specific
  branching must occur only in composition roots (``bootstrap/``,
  ``adapters/``).  Call-sites of ``get_current_env()``, ``is_dev()``,
  ``is_prod()``, and ``get_local_ip()`` outside those layers are policy
  violations because environment decisions should remain injectable rather
  than runtime-detected.

KNOWN PRE-EXISTING VIOLATIONS (documented in RULES-MANIFEST.md):
- 21 core files call env_utils functions directly
- 9 adapter files also use them (expected — adapters are composition roots)

This test is a POLICY DOCUMENTATION tool: it always warns, never fails.
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

# Layers where env-check function calls are expected / acceptable.
ALLOWED_LAYERS = {"bootstrap", "adapters", "plugins", "infra", "logger", "config"}

# Layers that should NOT contain env-check calls.
# These are architectural violations that need to be migrated into bootstrap
# configuration injection.
WARN_LAYERS = {"core", "api", "spi"}

# Functions whose call-sites we track.
# All live in ``community.core.utils.env_utils``.
_ENV_CHECK_FUNCTIONS = frozenset(
    {"get_current_env", "is_dev", "is_prod", "get_local_ip"}
)

# The attribute-qualified pattern: ``env_utils.get_current_env()`` etc.
_MODULE_QUALIFIER = "env_utils"


def _find_env_check_calls(
    scan_dir: Path,
) -> dict[str, list[tuple[str, int, str]]]:
    """Scan *scan_dir* for environment-check function calls.

    Returns dict mapping layer name (e.g. ``'core'``, ``'adapters'``) to
    a list of ``(rel_path, lineno, call_text)`` tuples.

    Detects two call patterns:

    * ``get_current_env()`` — bare call (``ast.Call`` with ``ast.Name`` func).
    * ``env_utils.get_current_env()`` — qualified call (``ast.Call`` with
      ``ast.Attribute`` func whose ``attr`` matches and ``value.id`` is
      ``"env_utils"``).

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

        rel_path = py_file.relative_to(SECBAAS)
        layer = rel_path.parts[0]

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            call_text: str | None = None

            # Pattern 1: bare call  e.g.  get_current_env()
            if isinstance(node.func, ast.Name) and node.func.id in _ENV_CHECK_FUNCTIONS:
                call_text = node.func.id

            # Pattern 2: qualified call  e.g.  env_utils.get_current_env()
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _ENV_CHECK_FUNCTIONS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == _MODULE_QUALIFIER
            ):
                call_text = f"{node.func.value.id}.{node.func.attr}"

            if call_text is not None:
                lineno = getattr(node, "lineno", 0)
                results.setdefault(layer, []).append((str(rel_path), lineno, call_text))

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_env_branching_confined_to_allowed_layers():
    """Rule 14: env-check calls in core/api/spi should be injected via bootstrap.

    Scans all layers for ``get_current_env()``, ``is_dev()``, ``is_prod()``,
    and ``get_local_ip()`` call-sites.

    * Calls in ``ALLOWED_LAYERS`` are silently counted.
    * Calls in ``WARN_LAYERS`` (core, api, spi) emit a ``warnings.warn()``
      with the file path, line number, and call text.

    This test NEVER fails — it is a policy-documentation and migration-guide
    tool, not an invariant check.
    """
    call_results = _find_env_check_calls(SECBAAS)

    allowed_count = 0
    warn_items: list[str] = []

    for layer in sorted(call_results):
        for rel_path, lineno, call_text in call_results[layer]:
            if layer in ALLOWED_LAYERS:
                allowed_count += 1
            elif layer in WARN_LAYERS:
                warn_items.append(f"  {layer:>9s}  {rel_path}:{lineno}  {call_text}()")

    if warn_items:
        warnings.warn(
            f"\nRule 14: {len(warn_items)} env-check call-site(s) found in "
            f"non-wiring layers (core/api/spi).\n"
            f"These calls should be injected via bootstrap configuration "
            f"rather than detected at runtime.\n\n"
            f"Violations:\n"
            + "\n".join(sorted(warn_items))
            + "\n\n(See RULES-MANIFEST.md for the Rule 14 waiver log.)"
        )


def test_no_env_check_in_non_allowed_layers():
    """Rule 14 watchdog: env-check call-sites must not proliferate.

    Counts total env-check call-sites in ``WARN_LAYERS`` (core, api, spi).
    If the count exceeds the known baseline (0 — all existing call-sites
    should be in allowed layers per the env-import scan data), a warning
    is emitted.

    This is a soft check — always warns, never fails.
    """
    call_results = _find_env_check_calls(SECBAAS)

    total_warn = 0
    for layer in WARN_LAYERS:
        total_warn += len(call_results.get(layer, []))

    # Document every call-site for audit trail visibility.
    if total_warn > 0:
        detail_lines: list[str] = []
        for layer in sorted(WARN_LAYERS & set(call_results)):
            for rel_path, lineno, call_text in call_results[layer]:
                detail_lines.append(
                    f"  {layer:>9s}  {rel_path}:{lineno}  {call_text}()"
                )

        warnings.warn(
            f"\nRule 14 watchdog: {total_warn} env-check call-site(s) "
            f"remain in WARN_LAYERS (core/api/spi).\n"
            f"Baseline target: 0 (all env branching in bootstrap/adapters).\n\n"
            f"Current call-sites:\n"
            + "\n".join(sorted(detail_lines))
            + "\n\nThese should be refactored to receive environment config "
            "via bootstrap injection rather than calling env_utils directly. "
            "Update RULES-MANIFEST.md when debt is paid down."
        )
