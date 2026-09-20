"""Service API Protocol for public-bot discovery (search + recommend)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogCaller,
    BotCatalogSearchFilters,
)


@runtime_checkable
class BotDiscoverServiceProtocol(Protocol):
    """Service API for public bot discovery."""

    def search_by_keyword(
        self,
        keyword: str,
        user_id: str | None = None,
        top_k: int = 10,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
        catalog_filters: BotCatalogSearchFilters | None = None,
        caller: BotCatalogCaller | None = None,
        request_id: str = "internal-discover",
        runtime_state: str | None = None,
        viewer_actor_type: str | None = None,
        viewer_actor_id: str | None = None,
    ) -> dict[str, Any]: ...
