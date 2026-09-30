"""Architecture enforcement: contract isolation rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 3** — Service APIs and Plugin APIs are distinct contract types.
  ``gateway.community.api`` (Service API) must not import from
  ``gateway.community.spi`` (Plugin API).

- **Rule 5** — Contracts are separate from implementations.  Contract
  layers (``api/``, ``spi/``) must not import concrete implementation
  modules (``core/``, ``plugins/``).

KNOWN LIMITATION: pytestarch only catches module-level imports;
lazy/function-body imports are not scanned.
"""

from pytestarch import Rule


def _check_bans(layer: str, ban: str, project_architecture) -> None:  # noqa: ANN001
    """Assert *layer* modules do not module-level-import *ban*."""
    rule = (
        Rule()
        .modules_that()
        .are_sub_modules_of(layer)
        .should_not()
        .import_modules_that()
        .are_sub_modules_of(ban)
    )
    try:
        rule.assert_applies(project_architecture)
    except AssertionError as exc:
        lines = exc.args[0].splitlines() if exc.args else []
        raise AssertionError(
            f"\nlayer={layer!r} banned from importing {ban!r}:" + "\n".join(lines)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 3: Service API must not import Plugin API
# ═══════════════════════════════════════════════════════════════════════════


def test_api_does_not_import_spi(project_architecture):  # noqa: ANN001, ANN201
    """Rule 3: Service API (api) must not import Plugin API (spi) modules."""
    _check_bans(
        "gateway.community.api",
        "gateway.community.spi",
        project_architecture,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 5: Contract layers must not import concrete implementations
# ═══════════════════════════════════════════════════════════════════════════


def test_api_does_not_import_core(project_architecture):  # noqa: ANN001, ANN201
    """Rule 5: Service API (api) must not import core services."""
    _check_bans(
        "gateway.community.api",
        "gateway.community.core",
        project_architecture,
    )


def test_api_does_not_import_plugins(project_architecture):  # noqa: ANN001, ANN201
    """Rule 5: Service API (api) must not import plugin implementations."""
    _check_bans(
        "gateway.community.api",
        "gateway.community.plugins",
        project_architecture,
    )


def test_spi_does_not_import_core(project_architecture):  # noqa: ANN001, ANN201
    """Rule 5: Plugin API (spi) must not import core services."""
    _check_bans(
        "gateway.community.spi",
        "gateway.community.core",
        project_architecture,
    )


def test_spi_does_not_import_plugins(project_architecture):  # noqa: ANN001, ANN201
    """Rule 5: Plugin API (spi) must not import plugin implementations."""
    _check_bans(
        "gateway.community.spi",
        "gateway.community.plugins",
        project_architecture,
    )
