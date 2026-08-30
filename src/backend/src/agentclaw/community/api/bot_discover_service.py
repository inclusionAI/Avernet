"""Service API Protocol for public-bot discovery (search + recommend).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_public/bot_discover_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_public.bot_discover_service_protocol import (
    BotDiscoverServiceProtocol,
)

__all__ = [
    "BotDiscoverServiceProtocol",
]
