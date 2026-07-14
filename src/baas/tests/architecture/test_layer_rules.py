"""Architecture enforcement: layer import rules.

Each layer declares what it MUST NOT import.  The rules are derived from
the Microkernel Architecture Constitution (Rules 6-8) and enforced via
pytestarch at module level (function-body lazy imports are not scanned).

Allowed dependency graph::

    adapters ──► api, bootstrap, logger, infra.utils, spi, config,
                 core.utils, core.database, core.cron, core.service.paas.desktop
    api      ──► api, spi
    spi      ──► api, spi
    core     ──► api, spi, core, infra
    plugins  ──► api, spi, infra, core.utils
    infra    ──► api, spi, infra, core.utils, config, logger
    config   ──► logger
    logger   ──► plugins, spi      (boot-time singleton bridge)
    bootstrap──► everything        (composition root — no bans)

**KNOWN TECH DEBT** (lazy imports inside function bodies, not caught here):
  - core/repository/*/_factory.py:  ``from community.bootstrap import get_container``
  - plugins/sandbox/utils/arca_utils.py:  ``from community.bootstrap import get_container``
"""

from pytestarch import Rule

# ═══════════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════════


def _check_bans(
    layer: str,
    bans: list[str],
    project_architecture,  # noqa: ANN001
    allow: list[str] | None = None,
) -> None:
    """Assert *layer* modules do not module-level-import anything from *bans*.

    Args:
        layer: Layer to check.
        bans: Modules that are banned.
        project_architecture: Pytestarch graph.
        allow: Sub-modules of bans that are allowed (e.g. "secbaas.community.core.utils").
    """
    allow = allow or []
    violations: list[str] = []
    for banned in bans:
        rule = (
            Rule()
            .modules_that()
            .are_sub_modules_of(layer)
            .should_not()
            .import_modules_that()
            .are_sub_modules_of(banned)
        )
        try:
            rule.assert_applies(project_architecture)
        except AssertionError as exc:
            # Filter out violations for allowed sub-modules
            filtered: list[str] = []
            for raw_line in str(exc).splitlines():
                cleaned = raw_line.replace('"', "")
                is_allowed = any(allowed_mod in cleaned for allowed_mod in allow)
                if not is_allowed:
                    filtered.append(raw_line)
            if filtered:
                violations.append("\n".join(filtered))
    if violations:
        msg_parts = [f"\n{layer!r} has {len(violations)} import ban violation(s):"]
        for banned in bans:
            banned_lines: list[str] = []
            for violation_text in violations:
                for raw_line in violation_text.splitlines():
                    cleaned = raw_line.replace('"', "")
                    if f'imports "{banned}' in raw_line:
                        banned_lines.append(f"    {cleaned}")
            if banned_lines:
                msg_parts.append(
                    f"  ── importing from {banned!r} ({len(banned_lines)} module(s)) ──"
                )
                for bl in banned_lines:
                    msg_parts.append(bl)
        if any("imports" in part for part in msg_parts[1:]):
            raise AssertionError("\n".join(msg_parts))


# ═══════════════════════════════════════════════════════════════════════════
# adapters
# ═══════════════════════════════════════════════════════════════════════════

_ADAPTER_BANS = [
    "secbaas.community.core",
]
_ADAPTER_ALLOW = [
    # Transitive through bootstrap (ApplicationContainer provider wiring).
    # The router references Provide[ApplicationContainer.repository.ws_relay_session_repository],
    # which requires importing ApplicationContainer from community.bootstrap, which transitively
    # imports core.repository.ws_relay_session.  This is the expected DI composition-root pattern.
    "core.repository.ws_relay_session",
]


def test_adapters_web_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    _check_bans(
        "secbaas.community.adapters.web",
        _ADAPTER_BANS,
        project_architecture,
        allow=_ADAPTER_ALLOW,
    )


# ═══════════════════════════════════════════════════════════════════════════
# api — contract layer
# ═══════════════════════════════════════════════════════════════════════════

_API_BANS = [
    "secbaas.community.adapters",
    "secbaas.community.plugins",
    "secbaas.community.bootstrap",
    "secbaas.community.core",
]


def test_api_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """api must not import adapters, plugins, bootstrap, or core."""
    _check_bans("secbaas.community.api", _API_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# spi — contract layer
# ═══════════════════════════════════════════════════════════════════════════

_SPI_BANS = [
    "secbaas.community.adapters",
    "secbaas.community.plugins",
    "secbaas.community.bootstrap",
    "secbaas.community.core",
]


def test_spi_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """spi must not import adapters, plugins, bootstrap, or core."""
    _check_bans("secbaas.community.spi", _SPI_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# core
# ═══════════════════════════════════════════════════════════════════════════

_CORE_BANS = [
    "secbaas.community.adapters",
    "secbaas.community.plugins",
    "secbaas.community.bootstrap",
]


def test_core_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """core must not import adapters, plugins, or bootstrap."""
    _check_bans("secbaas.community.core", _CORE_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# plugins
# ═══════════════════════════════════════════════════════════════════════════

_PLUGINS_BANS = [
    "secbaas.community.adapters",
    "secbaas.community.core",
    "secbaas.community.bootstrap",
]
_PLUGINS_ALLOW = [
    "secbaas.community.core.utils",
    # TECH DEBT: _seed.py needs model access for tenant/device_template seeding.
    # Fix: move seed models to spi layer or use loose coupling.
    "secbaas.community.core.repository.device_template",
    "secbaas.community.core.repository.tenant",
    # TECH DEBT: sqlite_orm.init_database registers with db_manager.
    # Fix: move db_manager registration back to lifecycle or allow plugin→core.database.
    "secbaas.community.core.database",
]


def test_plugins_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """plugins must not import adapters, core (except core.utils), or bootstrap."""
    _check_bans(
        "secbaas.community.plugins",
        _PLUGINS_BANS,
        project_architecture,
        allow=_PLUGINS_ALLOW,
    )


# ═══════════════════════════════════════════════════════════════════════════
# config — pure config loader utility
# ═══════════════════════════════════════════════════════════════════════════

_CONFIG_BANS = [
    "secbaas.community.adapters",
    "secbaas.community.plugins",
    "secbaas.community.core",
    "secbaas.community.bootstrap",
]


def test_config_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """config must not import adapters, plugins, core, or bootstrap."""
    _check_bans("secbaas.community.config", _CONFIG_BANS, project_architecture)
