"""Service API contract for changing a Bot's owning Space.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_management/bot_space_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_management.bot_space_service_protocol import (
    BotSpaceAssignmentResult,
    BotSpaceServiceProtocol,
)

__all__ = [
    "BotSpaceAssignmentResult",
    "BotSpaceServiceProtocol",
]
