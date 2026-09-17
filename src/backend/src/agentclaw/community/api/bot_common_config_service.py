"""Service API Protocol for per-Bot common configuration.

Re-export only. The Protocol is defined in its owning core module
(``core/common_config/bot_config_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.common_config.bot_config_protocol import (
    BotCommonConfigEntry,
    BotCommonConfigServiceProtocol,
)

__all__ = [
    "BotCommonConfigEntry",
    "BotCommonConfigServiceProtocol",
]
