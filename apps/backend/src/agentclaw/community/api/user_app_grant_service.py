"""Service API Protocol for user-level application delegations.

Re-export only. The Protocol is defined beside the bot grant's, in
``core/bot_app_grant/bot_app_grant_service_protocol.py``, so the concrete
service can inherit it without a ``core -> api`` waiver; adapters keep
importing it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_app_grant.bot_app_grant_service_protocol import (
    UserAppGrantRecord,
    UserAppGrantServiceProtocol,
)

__all__ = [
    "UserAppGrantRecord",
    "UserAppGrantServiceProtocol",
]
