"""BareTenantResolver — open-source single-box stub for the tenant resolver.

Maps any non-empty tenant token to the fixed ``tenant-bare``. NOT real
validation — production uses the sofa flavor (a tenant-token registry).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import TenantResolver

_BARE_TENANT = "tenant-bare"


class BareTenantResolver(TenantResolver):
    """Single-box stub: one fixed tenant."""

    async def resolve(self, tenant_token: str) -> str:
        if not tenant_token:
            raise AuthError("missing tenant token")
        return _BARE_TENANT
