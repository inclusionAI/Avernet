"""Service API for the default-bot Passport repair operation.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_management/default_bot_passport_repair_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_management.default_bot_passport_repair_service_protocol import (
    DefaultBotPassportRepairServiceProtocol,
)

__all__ = [
    "DefaultBotPassportRepairServiceProtocol",
]
