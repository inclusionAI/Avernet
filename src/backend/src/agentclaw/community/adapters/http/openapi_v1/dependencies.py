"""Auth seam for the public API (definition-only).

The gateway authenticates the caller and forwards a signed principal; the
backend verifies it. That verifier is the auth workstream's deliverable — here
``require_principal`` is a stub so the routers are definition-complete and the
seam exists for the real implementation to drop into.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

# Placeholder principal type for the definition-only stubs.
Principal = Any


async def require_principal() -> Principal:
    """Stub principal dependency — replaced by the gateway-JWT verifier."""
    return None


def resolve_avernet_tenant(request: Request) -> str:
    """Return the data-isolation tenant a public-API request belongs to.

    The single replaceable seam for the public API's tenant, mirroring
    ``require_principal``: one implementation for every deploy profile (there is
    one gateway contract), replaced in place — not a per-profile DI binding.

    Placeholder for now — every public request resolves to the default tenant,
    so Stage 1 wires the seam without changing behavior. When the gateway
    forwards a verified principal, the auth workstream reads the tenant from
    ``request`` here; no endpoint or middleware changes when it lands.
    """
    del request  # unused until the real verifier reads the forwarded principal
    return DEFAULT_AVERNET_TENANT
