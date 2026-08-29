"""Service API Protocol for bot-chat session listing + health.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_chat/bot_chat_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_chat.bot_chat_service_protocol import (
    BotChatServiceProtocol,
    ConversationDetail,
    HealthCheckData,
    OpenBotChatServiceProtocol,
    SessionListResponse,
)

__all__ = [
    "BotChatServiceProtocol",
    "ConversationDetail",
    "HealthCheckData",
    "OpenBotChatServiceProtocol",
    "SessionListResponse",
]
