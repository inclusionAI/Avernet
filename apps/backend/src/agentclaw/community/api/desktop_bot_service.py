"""Service API Protocol for desktop bot lifecycle.

Re-export only. The Protocol is defined in its owning core module
(``core/desktop_bot/desktop_bot_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.desktop_bot.desktop_bot_service_protocol import (
    DesktopBotServiceProtocol,
)

__all__ = [
    "DesktopBotServiceProtocol",
]
