"""Public Service API for reading a Bot's active capability state.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/bot_capability_state_reader_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.bot_capability_state_reader_protocol import (
    BotCapabilityStateReaderProtocol,
    CoreBotCapabilityStateReaderProtocol,
)

__all__ = [
    "BotCapabilityStateReaderProtocol",
    "CoreBotCapabilityStateReaderProtocol",
]
