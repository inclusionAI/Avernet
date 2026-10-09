"""Service API Protocol for owner-granted bot authorizations.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_app_grant/bot_app_grant_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_app_grant.bot_app_grant_service_protocol import (
    BotAppGrantRecord,
    BotAppGrantServiceProtocol,
)

__all__ = [
    "BotAppGrantRecord",
    "BotAppGrantServiceProtocol",
]
