"""Service API Protocol for the non-user Bot overview.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_overview/bot_overview_service_protocol.py``).
"""

from __future__ import annotations

from agentclaw.community.core.bot_overview.bot_overview_service_protocol import (
    BotOverviewNotFoundError,
    BotOverviewServiceProtocol,
)

__all__ = [
    "BotOverviewNotFoundError",
    "BotOverviewServiceProtocol",
]
