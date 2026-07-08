"""HTTP adapter for economy/governance endpoints."""
from agentclaw.community.adapters.http.economy import (
    admin_router,  # noqa: F401
    router,  # noqa: F401
)


__all__ = ["admin_router", "router"]
