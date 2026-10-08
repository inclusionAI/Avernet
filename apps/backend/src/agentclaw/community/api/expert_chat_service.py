"""Service API Protocol for expert-chat session management.

Re-export only. The Protocol is defined in its owning core module
(``core/expert_chat/expert_chat_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.expert_chat.expert_chat_service_protocol import (
    ExpertChatServiceProtocol,
)

__all__ = [
    "ExpertChatServiceProtocol",
]
