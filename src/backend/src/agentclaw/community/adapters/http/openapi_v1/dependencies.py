"""Auth seam for the public API (definition-only).

The gateway authenticates the caller and forwards a signed principal; the
backend verifies it. That verifier is the auth workstream's deliverable — here
``require_principal`` is a stub so the routers are definition-complete and the
seam exists for the real implementation to drop into.
"""

from __future__ import annotations

from typing import Any

# Placeholder principal type for the definition-only stubs.
Principal = Any


async def require_principal() -> Principal:
    """Stub principal dependency — replaced by the gateway-JWT verifier."""
    return None
