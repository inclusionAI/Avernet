"""Authn dependency ports — the SPIs the identity strategies depend on.

Each strategy calls a port it does not implement; flavors (``bare`` / ``sofa``)
swap the implementation via ``PluginAccessor`` (Rule 14). These are
``Protocol`` classes (behaviour contracts) plus the ``dataclass`` records they
return.

The bot / access-key registries now live in their own domain SPIs
(``gateway.community.spi.bot`` / ``gateway.community.spi.access_key``); this
module keeps the app-token + tenant ports used by the ``app_token`` strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AppTokenRecord:
    """A validated app-token record (backed by baas api-gateway)."""

    app_id: str  # baas app_id
    app_name: str  # human-facing app name
    owners: str  # owning developer/org
    app_type: str
    tenant: str  # cross-checked with the presented tenant token


class AppTokenValidator(Protocol):
    """Verify an app token. ``None`` = no match; never raise on a bad token.

    Callers MUST treat ``None`` as 'this credential is not one of mine' (absent),
    not as 'invalid'.
    """

    async def verify(self, app_token: str) -> AppTokenRecord | None: ...


class TenantResolver(Protocol):
    """Verify a per-tenant token and map it to a tenant id.

    Missing/invalid token raises :class:`~gateway.community.spi.auth.AuthError`.
    """

    async def resolve(self, tenant_token: str) -> str: ...
