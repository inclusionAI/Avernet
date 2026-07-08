"""Architecture enforcement: contract isolation rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 3** — Service APIs and Plugin APIs are distinct contract types.
  ``secbaas.api`` (Service API) must not import from ``secbaas.spi``
  (Plugin API).  SPI protocols may reference API types under
  ``TYPE_CHECKING`` guards — those are intentional, not violations.

- **Rule 5** — Contracts are separate from implementations.  Contract
  layers (``api/``, ``spi/``) must not import concrete implementation
  modules (``core/``, ``plugins/``, ``infra/``).

KNOWN LIMITATION: pytestarch only catches module-level imports;
lazy/function-body imports are not scanned.
"""

from pathlib import Path

from pytestarch import Rule


def _check_bans(layer: str, ban: str, project_architecture) -> None:
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


def test_api_does_not_import_spi(project_architecture):
    """Rule 3: Service API (api) must not import Plugin API (spi) modules."""
    _check_bans("secbaas.api", "secbaas.spi", project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# Rule 5: Contract layers must not import concrete implementations
# ═══════════════════════════════════════════════════════════════════════════


def test_api_does_not_import_core(project_architecture):
    """Rule 5: Service API (api) must not import core services."""
    _check_bans("secbaas.api", "secbaas.core", project_architecture)


def test_api_does_not_import_plugins(project_architecture):
    """Rule 5: Service API (api) must not import plugin implementations."""
    _check_bans("secbaas.api", "secbaas.plugins", project_architecture)


def test_api_does_not_import_infra(project_architecture):
    """Rule 5: Service API (api) must not import infrastructure."""
    if not (Path(__file__).resolve().parents[2] / "src" / "secbaas" / "infra").is_dir():
        return  # infra/ removed — nothing to check
    _check_bans("secbaas.api", "secbaas.infra", project_architecture)


def test_spi_does_not_import_core(project_architecture):
    """Rule 5: Plugin API (spi) must not import core services."""
    _check_bans("secbaas.spi", "secbaas.core", project_architecture)


def test_spi_does_not_import_plugins(project_architecture):
    """Rule 5: Plugin API (spi) must not import plugin implementations."""
    _check_bans("secbaas.spi", "secbaas.plugins", project_architecture)


def test_spi_does_not_import_infra(project_architecture):
    """Rule 5: Plugin API (spi) must not import infrastructure."""
    if not (Path(__file__).resolve().parents[2] / "src" / "secbaas" / "infra").is_dir():
        return  # infra/ removed — nothing to check
    _check_bans("secbaas.spi", "secbaas.infra", project_architecture)
