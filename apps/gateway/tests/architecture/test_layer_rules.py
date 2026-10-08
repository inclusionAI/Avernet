"""Architecture enforcement: layer import rules.

Each layer declares what it MUST NOT import.  The rules are derived from
the Microkernel Architecture Constitution (Rules 6-8) and enforced via
pytestarch at module level (function-body lazy imports are not scanned).

Allowed dependency graph::

    adapters ──► api, bootstrap, logger, tracer, spi, config
    api      ──► api, spi
    spi      ──► api, spi
    core     ──► api, spi, core
    plugins  ──► api, spi
    config   ──► logger
    logger   ──► plugins, spi      (boot-time singleton bridge)
    tracer   ──► plugins, spi      (boot-time singleton bridge)
    bootstrap──► everything        (composition root — no bans)
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
        allow: Sub-modules of bans that are allowed.
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
    "gateway.community.core",
    "gateway.community.plugins",
    "gateway.community.bootstrap",
]


def test_adapters_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """adapters must not import core, plugins, or bootstrap."""
    _check_bans("gateway.community.adapters", _ADAPTER_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# api — contract layer
# ═══════════════════════════════════════════════════════════════════════════

_API_BANS = [
    "gateway.community.adapters",
    "gateway.community.plugins",
    "gateway.community.bootstrap",
    "gateway.community.core",
]


def test_api_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """api must not import adapters, plugins, bootstrap, or core."""
    _check_bans("gateway.community.api", _API_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# spi — contract layer
# ═══════════════════════════════════════════════════════════════════════════

_SPI_BANS = [
    "gateway.community.adapters",
    "gateway.community.plugins",
    "gateway.community.bootstrap",
    "gateway.community.core",
]


def test_spi_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """spi must not import adapters, plugins, bootstrap, or core."""
    _check_bans("gateway.community.spi", _SPI_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# core
# ═══════════════════════════════════════════════════════════════════════════

_CORE_BANS = [
    "gateway.community.adapters",
    "gateway.community.plugins",
    "gateway.community.bootstrap",
]


def test_core_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """core must not import adapters, plugins, or bootstrap."""
    _check_bans("gateway.community.core", _CORE_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# plugins
# ═══════════════════════════════════════════════════════════════════════════

_PLUGINS_BANS = [
    "gateway.community.adapters",
    "gateway.community.core",
    "gateway.community.api",
]


def test_plugins_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """plugins must not import adapters, core, or api."""
    _check_bans("gateway.community.plugins", _PLUGINS_BANS, project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# config — pure config loader utility
# ═══════════════════════════════════════════════════════════════════════════

_CONFIG_BANS = [
    "gateway.community.adapters",
    "gateway.community.plugins",
    "gateway.community.core",
    "gateway.community.bootstrap",
    "gateway.community.api",
    "gateway.community.spi",
]


def test_config_no_banned_imports(project_architecture):  # noqa: ANN001, ANN201
    """config must not import adapters, plugins, core, bootstrap, api, or spi."""
    _check_bans("gateway.community.config", _CONFIG_BANS, project_architecture)
