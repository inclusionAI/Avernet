"""Service API contract for space-scoped market favorites.

Re-export only. The Protocol is defined in its owning core module
(``core/market_favorites/market_favorite_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.market_favorites.market_favorite_service_protocol import (
    MarketFavoriteServiceProtocol,
)

__all__ = [
    "MarketFavoriteServiceProtocol",
]
