"""Service API Protocol for dormant Bot activation.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_dormant/bot_dormant_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_dormant.bot_dormant_service_protocol import (
    BotDormantActivateServiceProtocol,
)

__all__ = [
    "BotDormantActivateServiceProtocol",
]
