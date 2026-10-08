"""Service API Protocol for public-bot lifecycle + friend approvals.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_public/bot_public_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_public.bot_public_service_protocol import (
    BotPublicServiceProtocol,
)

__all__ = [
    "BotPublicServiceProtocol",
]
