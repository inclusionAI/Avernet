"""Composition of the auth subsystem (composition root, Rule 14).

Builds the database-backed identity registries (bot token, access-key token),
the identity-chain registry, the route-security table, and assembles an
:class:`Authenticator`. Only the composition root wires concrete plugins;
adapters receive the built ``Authenticator`` via ``app.state`` and never
import plugins or core.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

import yaml

from gateway.community.core.access_key import AccessKeyRepository, AccessKeyRow
from gateway.community.core.authn import Authenticator, IdentityChain, RouteSecurity
from gateway.community.core.bot import BotRepository, BotRow
from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import AppTokenStrategy
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.authn import (
    AppTokenValidator,
    AuthStrategy,
    PrincipalType,
    TenantResolver,
)
from gateway.community.spi.database import DataSourcePlugin

_logger = logging.getLogger("bootstrap")

_DEFAULT_TENANT = "default"
# Fail-closed default: every route requires an authenticated user.
_DEFAULT_TABLE = {"/**": {"user": "required"}}


def build_database(db_plugin: DataSourcePlugin) -> DataSourcePlugin:
    """Init the database plugin (``create_all`` + seed demo authn rows). Idempotent.

    Args:
        db_plugin: A container-resolved ``DataSourcePlugin``.
    """
    from gateway.community.bootstrap._configs import DatabasePluginConfig

    db_plugin.init_database(DatabasePluginConfig(plugin_type="SQLITE_ORM", db_url=""))
    _seed_authn(db_plugin)
    return db_plugin


def _seed_authn(db: DataSourcePlugin) -> None:
    """Idempotently seed the demo bot / access-key rows used by the bare edition.

    A bare/community convenience (sofa has real data and need not seed); lives
    in the composition root, not in the (flavor-neutral) domain modules.
    """
    with db.orm_session() as session:
        if session.get(BotRow, "bot-key") is None:
            session.add(
                BotRow(
                    token="bot-key",
                    bot_uuid="bot-7",
                    owner_id="owner-1",
                    tenant="t",
                )
            )
        if session.get(AccessKeyRow, "ak-token") is None:
            session.add(
                AccessKeyRow(
                    token="ak-token",
                    access_key_id="ak-1",
                    tenant="t",
                    expire_at=datetime(2027, 1, 1, 0, 0, 0),
                )
            )


def _default_strategy_pool(
    db: DataSourcePlugin,
    app_token_validator: AppTokenValidator,
    tenant_resolver: TenantResolver,
) -> dict[str, AuthStrategy]:
    """The built-in strategy instances, keyed by their short name."""
    return {
        "google": GoogleUserStrategy(
            token_header="x-google-token", default_tenant=_DEFAULT_TENANT
        ),
        "bot_token": BotTokenStrategy(registry=BotRepository(db)),
        "app_token": AppTokenStrategy(
            keys=app_token_validator, tenants=tenant_resolver
        ),
        "access_key_token": AccessKeyTokenStrategy(registry=AccessKeyRepository(db)),
    }


def _default_chains(
    pool: dict[str, AuthStrategy],
) -> dict[PrincipalType, IdentityChain]:
    return {
        PrincipalType.USER: IdentityChain(PrincipalType.USER, (pool["google"],)),
        PrincipalType.BOT: IdentityChain(PrincipalType.BOT, (pool["bot_token"],)),
        PrincipalType.APP: IdentityChain(PrincipalType.APP, (pool["app_token"],)),
        PrincipalType.ACCESS_KEY: IdentityChain(
            PrincipalType.ACCESS_KEY, (pool["access_key_token"],)
        ),
    }


def build_authenticator(
    db: DataSourcePlugin,
    app_token_validator: AppTokenValidator,
    tenant_resolver: TenantResolver,
) -> Authenticator:
    """Build the identity-chain registry + route table (once, from create_app).

    All parameters are required — the caller must resolve every dependency
    through the DI container.
    """
    return Authenticator(
        strategies=_strategy_chains(db, app_token_validator, tenant_resolver),
        route_security=_load_route_security(),
    )


def _strategy_chains(
    db: DataSourcePlugin,
    app_token_validator: AppTokenValidator,
    tenant_resolver: TenantResolver,
) -> dict[PrincipalType, IdentityChain]:
    """Parse identity_strategies.yaml, wiring each declared strategy by name."""
    pool = _default_strategy_pool(db, app_token_validator, tenant_resolver)
    defaults = _default_chains(pool)
    configs_dir = _resolve_configs_dir()
    if configs_dir is None:
        raise FileNotFoundError("configs directory not found — set GATEWAY_CONFIG_PATH")
    path = configs_dir / "identity_strategies.yaml"
    if not path.exists():
        raise FileNotFoundError(f"required config file not found: {path}")

    _logger.info("loading identity strategies from %s", path)
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text()) or {})
    declared = cast(dict[str, list[str]], raw.get("identity_strategies", {}) or {})
    chains: dict[PrincipalType, IdentityChain] = {}
    for identity_value, names in declared.items():
        try:
            identity = PrincipalType(identity_value)
        except ValueError as exc:
            raise KeyError(
                f"unknown identity '{identity_value}' in identity_strategies.yaml"
            ) from exc
        declared_chain: list[AuthStrategy] = []
        for name in names or []:
            if name not in pool:
                raise KeyError(
                    f"unknown strategy '{name}' for identity '{identity.value}' "
                    f"in identity_strategies.yaml"
                )
            declared_chain.append(pool[name])
        chains[identity] = IdentityChain(identity, tuple(declared_chain))
    for identity, default_chain in defaults.items():
        chains.setdefault(identity, default_chain)

    _logger.info(
        "identity strategies (%s): %d chains\n%s",
        path.name,
        len(chains),
        "\n".join(
            f"  {idty.value}: [{', '.join(s.name for s in chain._strategies)}]"
            for idty, chain in chains.items()
        ),
    )
    return chains


def _load_route_security() -> RouteSecurity:
    configs_dir = _resolve_configs_dir()
    if configs_dir is None:
        raise FileNotFoundError("configs directory not found — set GATEWAY_CONFIG_PATH")
    path = configs_dir / "route_security.yaml"
    if not path.exists():
        raise FileNotFoundError(f"required config file not found: {path}")
    _logger.info("loading route security from %s", path)
    rules = RouteSecurity.from_yaml(path)
    _logger.info(
        "route security (%s): %d routes\n%s",
        path.name,
        len(rules._rules),
        "\n".join(
            f"  {rule.method or '*'} /{'/'.join(rule.segments)} → "
            + str({idty.value: pres.value for idty, pres in rule.requirement.items()})
            for rule in rules._rules
        ),
    )
    return rules


def _resolve_configs_dir():
    from ._configs import resolve_configs_dir as _rcd

    return _rcd()
