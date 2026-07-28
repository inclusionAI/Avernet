"""Composition of the auth subsystem (composition root, Rule 14).

Builds the database-backed identity registries (bot token, access-key token)
resolved via the ``database`` plugin, the identity-chain registry (a
``dict[PrincipalType, IdentityChain]`` keyed by identity, each chain wrapping
the ordered strategies declared in ``identity_strategies.yaml``), the
route-security table, and exposes an :class:`Authenticator` that ties them to
the core runner. Only the composition root wires concrete plugins; adapters
receive the built ``Authenticator`` via ``app.state`` and never import plugins
or core.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml

from gateway.community.core.access_key import AccessKeyRepository, AccessKeyRow
from gateway.community.core.authn import IdentityChain, RouteSecurity
from gateway.community.core.authn import authenticate as run_auth
from gateway.community.core.bot import BotRepository, BotRow
from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
from gateway.community.plugins.authn.app_token import (
    AppTokenStrategy,
    BareAppTokenValidator,
    BareTenantResolver,
)
from gateway.community.plugins.authn.bot_token import BotTokenStrategy
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.plugins.database.bare import BareDatabasePlugin
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AppTokenValidator,
    AuthStrategy,
    CredentialBundle,
    Principal,
    PrincipalType,
    TenantResolver,
)
from gateway.community.spi.database import DataSourcePlugin

_DEFAULT_TENANT = "default"
# Fail-closed default: every route requires an authenticated user.
_DEFAULT_TABLE = {"/**": {"user": "required"}}

_app_token_plugin = PluginAccessor[AppTokenValidator](
    "gateway.auth.app_token", BareAppTokenValidator
)
_tenant_plugin = PluginAccessor[TenantResolver](
    "gateway.auth.tenant", BareTenantResolver
)
_db_plugin = PluginAccessor[DataSourcePlugin]("gateway.database", BareDatabasePlugin)


@dataclass(frozen=True)
class _DbInitConfig:
    """Minimal :class:`DatabasePluginConfig` for the bare SQLite plugin.

    An empty ``db_url`` lets :class:`BareDatabasePlugin` default to
    ``sqlite:///:memory:`` (or the ``DATABASE_URL`` env var).
    """

    plugin_type: str = "SQLITE_ORM"
    db_url: str = ""


@dataclass(frozen=True)
class Authenticator:
    """Resolves a route's identity requirement and runs its identity chains."""

    strategies: dict[PrincipalType, IdentityChain]
    route_security: RouteSecurity

    async def authenticate(
        self, method: str, path: str, creds: CredentialBundle
    ) -> dict[PrincipalType, Principal]:
        requirement = self.route_security.resolve(method, path)
        if requirement is None:  # fail-closed: no policy → deny
            raise AuthError("no auth policy for route")
        return await run_auth(creds, requirement, self.strategies)  # type: ignore[no-any-return]


def build_database() -> DataSourcePlugin:
    """Init the database plugin (``create_all`` + seed demo authn rows). Idempotent.

    Importing the ``_orm`` modules (top of this file) registers the ``bots`` /
    ``access_keys`` tables on ``Base.metadata`` so ``init_database``'s
    ``create_all`` creates them. The seeded rows persist for the plugin's
    engine life (``_db_plugin.get()`` caches the one in-memory SQLite).
    """
    db = _db_plugin.get()
    db.init_database(_DbInitConfig())  # create_all (idempotent) + bare seed (no-op)
    _seed_authn(db)  # idempotent demo rows for bot_token / access_key_token
    return db


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


def _default_strategy_pool(db: DataSourcePlugin) -> dict[str, AuthStrategy]:
    """The built-in strategy instances, keyed by their short name."""
    return {
        "google": GoogleUserStrategy(
            token_header="x-google-token", default_tenant=_DEFAULT_TENANT
        ),
        "bot_token": BotTokenStrategy(registry=BotRepository(db)),
        "app_token": AppTokenStrategy(
            keys=_app_token_plugin.get(), tenants=_tenant_plugin.get()
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


def build_authenticator(db: DataSourcePlugin | None = None) -> Authenticator:
    """Build the identity-chain registry + route table (once, from create_app)."""
    if db is None:
        db = build_database()
    return Authenticator(
        strategies=_strategy_chains(db), route_security=_load_route_security()
    )


def _strategy_chains(
    db: DataSourcePlugin | None = None,
) -> dict[PrincipalType, IdentityChain]:
    """Parse identity_strategies.yaml, wiring each declared strategy by name."""
    if db is None:
        # Chain construction does not query the DB, so the cached (possibly
        # un-init'd) plugin is enough here; callers that actually resolve
        # identities go through build_authenticator(), which inits+seeds first.
        db = _db_plugin.get()
    pool = _default_strategy_pool(db)
    defaults = _default_chains(pool)
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "identity_strategies.yaml" if configs_dir else None
    if path is None or not path.exists():
        return defaults

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
    # Fall back to defaults for any identity not declared in config.
    for identity, default_chain in defaults.items():
        chains.setdefault(identity, default_chain)
    return chains


def _load_route_security() -> RouteSecurity:
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "route_security.yaml" if configs_dir else None
    if path is not None and path.exists():
        return RouteSecurity.from_yaml(path)
    return RouteSecurity.from_table(_DEFAULT_TABLE)


def _resolve_configs_dir() -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else p.parent
    cwd = Path.cwd() / "configs"
    return cwd if cwd.exists() else None
