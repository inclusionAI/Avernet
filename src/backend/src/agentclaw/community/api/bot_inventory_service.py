"""Service API for Bot inventory aggregation."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.bot_inventory.types import (
    BotInventoryItem,
    BusinessSpaceRef,
    DeployMode,
)


@runtime_checkable
class BotInventoryServiceProtocol(Protocol):
    """User-scoped Bot inventory read model service."""

    def list_items(
        self,
        *,
        owner_id: str,
        space: BusinessSpaceRef | None,
        keyword: str | None,
        engine: str | None,
        deploy_mode: DeployMode | None,
        page: int,
        page_size: int,
    ) -> tuple[list[BotInventoryItem], int]: ...

    def get_item(
        self,
        *,
        owner_id: str,
        bot_id: str,
        space: BusinessSpaceRef | None,
    ) -> BotInventoryItem: ...
