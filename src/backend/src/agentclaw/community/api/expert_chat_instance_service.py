"""Service API Protocol for caller container instance lifecycle.

Re-export only. The Protocol is defined in its owning core module
(``core/expert_chat/expert_chat_instance_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.expert_chat.expert_chat_instance_service_protocol import (
    ExpertChatInstanceServiceProtocol,
)

__all__ = [
    "ExpertChatInstanceServiceProtocol",
]
