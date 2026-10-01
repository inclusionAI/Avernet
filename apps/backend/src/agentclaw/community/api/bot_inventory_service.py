"""Service API for Bot inventory aggregation.

Re-export only. The Protocol is defined in its owning core module
(``core/bot_inventory/bot_inventory_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_inventory.bot_inventory_service_protocol import (
    BotInventoryItem,
    BotInventoryServiceProtocol,
    BusinessSpaceRef,
    DeployMode,
)

__all__ = [
    "BotInventoryItem",
    "BotInventoryServiceProtocol",
    "BusinessSpaceRef",
    "DeployMode",
]
