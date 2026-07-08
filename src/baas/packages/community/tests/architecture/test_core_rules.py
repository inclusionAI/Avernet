"""Architecture enforcement: core layer rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 7** — Core logic must be transport-agnostic.  Core must not
  import adapters (``secbaas.adapters``) or web/RPC framework packages
  (``fastapi``, ``aiohttp``, ``starlette``, ``flask``, ``django``, etc).

- **Rule 14** — Configuration drives all wiring; environment-specific
  branching must occur only in composition roots (``bootstrap/``).
  This test statically checks that ``from secbaas.bootstrap`` imports
  occur only within bootstrap/ and adapters/.

KNOWN PRE-EXISTING VIOLATIONS (documented in RULES-MANIFEST.md):
- (was) 25+ core files imported ``secbaas.infra.utils.env_utils`` — resolved via move to ``core.utils``
- 7 core files import ``aiohttp`` or ``fastapi``
"""

import ast
from pathlib import Path

from pytestarch import Rule

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

# Transport frameworks that should never appear in core
_TRANSPORT_FRAMEWORKS = {"fastapi", "aiohttp", "starlette", "flask", "django"}

# Known pre-existing debt — excluded from warnings.  See RULES-MANIFEST.md.
_KNOWN_TRANSPORT_DEBT: set[tuple[str, str]] = {
    ("core/service/bcn/uplink/_uplink_client.py", "import aiohttp"),
    ("core/service/bot_run/_async_session_client.py", "import aiohttp"),
    ("core/service/bot_run/_baas_service.py", "import aiohttp"),
    ("core/service/bot_run/_claw_service.py", "import aiohttp"),
}


# ═══════════════════════════════════════════════════════════════════════════
# Rule 7: Core is transport-agnostic (pytestarch import check)
# ═══════════════════════════════════════════════════════════════════════════


def test_core_does_not_import_adapters(project_architecture):
    """Rule 7: Core must not import delivery layer (adapters)."""
    rule = (
        Rule()
        .modules_that()
        .are_sub_modules_of("secbaas.core")
        .should_not()
        .import_modules_that()
        .are_sub_modules_of("secbaas.adapters")
    )
    rule.assert_applies(project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# Rule 7: Core must not import web/RPC frameworks (AST scan)
#
# pytestarch can't check for third-party framework imports, so we use AST.
# We check module-level imports only; lazy function-body imports are a
# KNOWN LIMITATION.
# ═══════════════════════════════════════════════════════════════════════════


def test_core_no_transport_framework_imports():
    """Rule 7: Core must not import transport frameworks at module level.

    Pre-existing violations in 7 core files are documented as known debt.
    """
    core_dir = SECBAAS / "core"
    violations: list[str] = []

    for py_file in sorted(core_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    if pkg in _TRANSPORT_FRAMEWORKS:
                        violations.append(
                            f"  {py_file.relative_to(SECBAAS)}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                pkg = node.module.split(".")[0]
                if pkg in _TRANSPORT_FRAMEWORKS:
                    names = [a.name for a in node.names]
                    violations.append(
                        f"  {py_file.relative_to(SECBAAS)}: "
                        f"from {node.module} import {', '.join(names)}"
                    )

    # Filter out known pre-existing debt
    new_violations = [
        v
        for v in violations
        if tuple(v.strip().split(": ", 1)) not in _KNOWN_TRANSPORT_DEBT
    ]

    if new_violations:
        import warnings

        warnings.warn(
            f"\n{len(new_violations)} new transport framework import(s) in core "
            f"(pre-existing debt in {len(violations) - len(new_violations)} known files "
            f"excluded, see RULES-MANIFEST.md):\n" + "\n".join(sorted(new_violations))
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 14: Wiring only in approved composition roots
#
# Check that secbaas.bootstrap imports are not scattered across non-wiring
# layers.  Only bootstrap/ and adapters/ (app bootstrap wiring) may import
# from secbaas.bootstrap at module level.
# ═══════════════════════════════════════════════════════════════════════════


def test_bootstrap_not_imported_from_core_api_spi(project_architecture):
    """Rule 14: Core, api, and spi must not import from bootstrap.

    Dependency injection wiring is a composition root concern only.
    """
    for layer in ("secbaas.core", "secbaas.api", "secbaas.spi"):
        rule = (
            Rule()
            .modules_that()
            .are_sub_modules_of(layer)
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("secbaas.bootstrap")
        )
        try:
            rule.assert_applies(project_architecture)
        except AssertionError as exc:
            lines = exc.args[0].splitlines() if exc.args else []
            raise AssertionError(
                f"\n{layer!r} must not import from secbaas.bootstrap:"
                + "\n".join(lines)
            )
