"""Service API contract for space-scoped market favorites."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.market_favorites.models import (
        FavoriteTargetType,
        MarketSource,
        MarketFavoriteRecord,
    )


@runtime_checkable
class MarketFavoriteServiceProtocol(Protocol):
    @abstractmethod
    def add(
        self,
        *,
        space_id: int,
        actor_id: str,
        market_source: MarketSource,
        target_type: FavoriteTargetType,
        target_code: str,
    ) -> tuple[MarketFavoriteRecord, bool]: ...

    @abstractmethod
    def cancel(
        self,
        *,
        space_id: int,
        actor_id: str,
        market_source: MarketSource,
        target_type: FavoriteTargetType,
        target_code: str,
    ) -> bool: ...

    @abstractmethod
    def search(
        self,
        *,
        space_id: int,
        actor_id: str,
        market_source: MarketSource | None,
        target_type: FavoriteTargetType | None,
        keyword: str | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[MarketFavoriteRecord]]: ...

    @abstractmethod
    def find_favorited_codes(
        self,
        *,
        space_id: int,
        actor_id: str,
        market_source: MarketSource,
        target_type: FavoriteTargetType,
        target_codes: list[str],
    ) -> list[str]: ...
