"""Composition of the auth subsystem (composition root, Rule 14).

Reads ``authn.yaml`` (type → ordered plugin chain), builds the strategy pool
(per flavor), validates names against the pool, and exposes an
:class:`Authenticator` that ties the registry + route table to the core runner.
Only the composition root wires concrete plugins and touches ``PluginAccessor``;
adapters receive the built ``Authenticator`` via ``app.state`` and never import
plugins or core. The core runner is flavor-agnostic and has no ``source``
awareness.

User identity comes only from a verified Google access token (the ``google``
strategy calls Google's userinfo endpoint, mirroring BCS ``bcs-auth-google``);
bot identity comes from ``bot_token``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gateway.community.core.authn import (
    Identities,
    RouteSecurity,
    build_strategy_registry,
    load_chains,
)
from gateway.community.core.authn import authenticate as run_auth
from gateway.community.plugins.authn.bot_token import (
    BotTokenStrategy,
    InMemoryBotRegistry,
)
from gateway.community.plugins.authn.google_token import GoogleUserStrategy
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import AuthStrategy, CredentialBundle, PrincipalType

_DEFAULT_TENANT = "default"
_USER_TOKEN_HEADER = "x-user-token"
_BOT_TOKEN_HEADER = "x-bot-token"


def _strategy_pool(
    google_transport: object | None,
) -> dict[str, AuthStrategy]:
    """The bare flavor's available strategies, keyed by authn.yaml name."""
    bot_registry = InMemoryBotRegistry()
    return {
        "google": GoogleUserStrategy(
            token_header=_USER_TOKEN_HEADER,
            default_tenant=_DEFAULT_TENANT,
            transport=google_transport,  # type: ignore[arg-type]
        ),
        "bot_token": BotTokenStrategy(
            registry=bot_registry, token_header=_BOT_TOKEN_HEADER
        ),
    }


@dataclass
class Authenticator:
    """Resolves a route's requirement and runs its strategies to Identities."""

    strategies: dict[PrincipalType, tuple[AuthStrategy, ...]]
    route_security: RouteSecurity

    async def authenticate(
        self, method: str, path: str, creds: CredentialBundle
    ) -> Identities:
        requirement = self.route_security.resolve(method, path)
        if requirement is None:  # fail-closed: no policy → deny
            raise AuthError("no auth policy for route")
        return await run_auth(creds, requirement, self.strategies)


def build_authenticator(*, google_transport: object | None = None) -> Authenticator:
    """Build the auth registry + route table (called once from ``create_app``).

    ``google_transport`` is an HTTP-transport seam: production omits it (real
    Google userinfo); tests pass an :class:`httpx.MockTransport` so the call is
    not made against the real endpoint.
    """
    chains = _load_chains()
    registry = build_strategy_registry(chains, _strategy_pool(google_transport))
    return Authenticator(strategies=registry, route_security=_load_route_security())


def _load_chains() -> dict[PrincipalType, list[str]]:
    """Load the type → strategy-name chains from ``authn.yaml`` if present.

    Falls back to a built-in default chain (mirroring the shipped
    ``configs/authn.yaml``) when no config directory is resolvable or the file is
    absent — e.g. when ``create_app`` runs with a cwd that has no ``configs/``.
    """
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "authn.yaml" if configs_dir else None
    if path is not None and path.exists():
        return load_chains(path)
    return dict(_DEFAULT_CHAINS)


# Built-in fallback (mirrors configs/authn.yaml).
_DEFAULT_CHAINS: dict[PrincipalType, list[str]] = {
    PrincipalType.USER: ["google"],
    PrincipalType.BOT: ["bot_token"],
}


def _load_route_security() -> RouteSecurity:
    configs_dir = _resolve_configs_dir()
    path = configs_dir / "route_security.yaml" if configs_dir else None
    if path is not None and path.exists():
        return RouteSecurity.from_yaml(path)
    return RouteSecurity.from_table({"/**": ["user"]})


def _resolve_configs_dir() -> Path | None:
    explicit = os.getenv("GATEWAY_CONFIG_PATH", "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else p.parent
    cwd = Path.cwd() / "configs"
    return cwd if cwd.exists() else None
