"""Service API for authoritative Human-to-Bot friendship reads.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_chat/human_bot_friendship_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_chat.human_bot_friendship_service_protocol import (
    HumanBotFriendshipServiceProtocol,
)

__all__ = [
    "HumanBotFriendshipServiceProtocol",
]
