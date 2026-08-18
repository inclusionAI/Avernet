"""Persistence contract for space-scoped market favorites."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.market_favorites.models import (
        FavoriteTargetType,
        MarketFavoriteRecord,
    )


@runtime_checkable
class MarketFavoriteRepositoryProtocol(Protocol):
    @abstractmethod
    def add(
        self,
        *,
        space_id: int,
        target_type: FavoriteTargetType,
        target_code: str,
        created_by: str,
        env: str,
    ) -> MarketFavoriteRecord: ...

    @abstractmethod
    def cancel(
        self,
        *,
        space_id: int,
        target_type: FavoriteTargetType,
        target_code: str,
        env: str,
    ) -> bool: ...

    @abstractmethod
    def search(
        self,
        *,
        space_id: int,
        target_type: FavoriteTargetType | None,
        keyword: str | None,
        env: str,
        offset: int,
        limit: int,
    ) -> tuple[int, list[MarketFavoriteRecord]]: ...
