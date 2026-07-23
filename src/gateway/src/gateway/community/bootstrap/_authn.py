"""Composition of the auth subsystem (composition root, Rule 14).

Builds the strategy registry and the route-security table, and exposes an
:class:`Authenticator` that ties them to the core runner. Only the composition
root wires concrete plugins and touches ``PluginAccessor``; adapters receive the
built ``Authenticator`` via ``app.state`` and never import plugins or core.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gateway.community.core.authn import RouteSecurity
from gateway.community.core.authn import authenticate as run_auth
from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.plugins.authn.first_party_user import FirstPartyUserStrategy
from gateway.community.spi.auth import AuthError, AuthPlugin
from gateway.community.spi.authn import AuthStrategy, CredentialBundle, Principal

# Fallback tenant when a first-party identity carries none (single-box default).
_DEFAULT_TENANT = "default"
# Fallback table when no config file is present: every route needs a user.
_DEFAULT_TABLE = {"/**": ["first_party_user"]}

_auth_plugin = PluginAccessor[AuthPlugin]("gateway.auth", BareAuthPlugin)


@dataclass(frozen=True)
class Authenticator:
    """Resolves a route's requirement and runs its strategies to a Principal."""

    strategies: dict[str, AuthStrategy]
    route_security: RouteSecurity

    async def authenticate(
        self, method: str, path: str, creds: CredentialBundle
    ) -> Principal:
        requirement = self.route_security.resolve(method, path)
        if requirement is None:  # fail-closed: no policy → deny
            raise AuthError("no auth policy for route")
        return await run_auth(creds, requirement, self.strategies)


def build_authenticator() -> Authenticator:
    """Build the auth registry + route table (called once from ``create_app``)."""
    strategies: dict[str, AuthStrategy] = {
        FirstPartyUserStrategy.name: FirstPartyUserStrategy(
            auth=_auth_plugin.get(), default_tenant=_DEFAULT_TENANT
        ),
    }
    return Authenticator(strategies=strategies, route_security=_load_route_security())


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
