"""Architecture enforcement: plugin isolation rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 15** — Plugin isolation.  Plugins are independently deployable
  units and MUST NOT import from one another at module level.  The
  composition root (``community.bootstrap``) is allowed to wire plugins
  together; cross-plugin knowledge belongs there, not inside plugins.

  This test verifies that:
  1. No plugin sub-package imports from any other plugin sub-package.
  2. ``community.bootstrap`` IS allowed to import from plugins (positive
     test verifying the composition-root exemption).

KNOWN LIMITATION: pytestarch only catches module-level imports;
lazy/function-body imports are not scanned.
"""

from pathlib import Path

from pytestarch import Rule

# ──────────────────────────────────────────────────────────────────
# Helper: dynamically discover plugin sub-packages
# ──────────────────────────────────────────────────────────────────

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"
_PLUGINS_DIR = GATEWAY / "plugins"


def _plugin_names() -> list[str]:
    """Return the names of all plugin sub-packages under ``plugins/``."""
    return sorted(
        d.name
        for d in _PLUGINS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    )


def _find_plugin_violations(project_architecture) -> list[str]:
    """For every plugin, check that it does not import any other plugin.

    Returns a list of human-readable violation descriptions.
    """
    plugins = _plugin_names()
    violations: list[str] = []

    for plugin in plugins:
        for other in plugins:
            if other == plugin:
                continue
            rule = (
                Rule()
                .modules_that()
                .are_sub_modules_of(f"gateway.community.plugins.{plugin}")
                .should_not()
                .import_modules_that()
                .are_sub_modules_of(f"gateway.community.plugins.{other}")
            )
            try:
                rule.assert_applies(project_architecture)
            except AssertionError as exc:
                lines = exc.args[0].splitlines() if exc.args else []
                for raw_line in lines:
                    cleaned = raw_line.replace('"', "")
                    violations.append(f"    {cleaned}")

    return violations


# ══════════════════════════════════════════════════════════════════
# Rule 15: Plugin isolation
# ══════════════════════════════════════════════════════════════════


def test_plugins_do_not_import_each_other(project_architecture):
    """Rule 15: Every plugin sub-package must be isolated — no module-level
    imports into any other plugin sub-package under ``community.plugins``.
    """
    violations = _find_plugin_violations(project_architecture)
    if violations:
        msg_parts = [
            f"\nPlugin isolation violations (Rule 15) — "
            f"{len(violations)} cross-plugin import(s) found:"
        ]
        msg_parts.extend(violations)
        raise AssertionError("\n".join(msg_parts))


def test_bootstrap_may_import_plugins(project_architecture):
    """Rule 15 (composition-root exemption): ``community.bootstrap`` IS
    permitted to import from ``community.plugins`` so it can wire the
    dependency container.
    """
    rule = (
        Rule()
        .modules_that()
        .are_sub_modules_of("gateway.community.bootstrap")
        .should()
        .import_modules_that()
        .are_sub_modules_of("gateway.community.plugins")
    )
    rule.assert_applies(project_architecture)
