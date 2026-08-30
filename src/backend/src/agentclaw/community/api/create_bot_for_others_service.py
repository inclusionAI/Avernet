"""Service API for provisioning another user's default bot.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_management/create_bot_for_others_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_management.create_bot_for_others_service_protocol import (
    CreateBotForOthersServiceProtocol,
)

__all__ = [
    "CreateBotForOthersServiceProtocol",
]
