"""Space-scoped market favorite service.

This phase stores only stable target references. Market metadata enrichment is
intentionally deferred with the Skill/MCP catalogue integration.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.market_favorites.errors import (
    FavoriteNotFoundError,
    FavoriteTargetInvalidError,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
)
from agentclaw.community.core.repository.protocols.market_favorites import (
    MarketFavoriteRepositoryProtocol,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.utils.env_utils import get_current_env


class MarketFavoriteService:
    @inject
    def __init__(
        self,
        repository: MarketFavoriteRepositoryProtocol,
        access: SpaceAccessService,
    ) -> None:
        self._repository = repository
        self._access = access

    @staticmethod
    def _target_code(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise FavoriteTargetInvalidError(
                "favorite target code must contain 1-128 characters"
            )
        return normalized

    def add(
        self,
        *,
        space_id: int,
        actor_id: str,
        target_type: FavoriteTargetType,
        target_code: str,
    ) -> MarketFavoriteRecord:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.add(
            space_id=space_id,
            target_type=target_type,
            target_code=self._target_code(target_code),
            created_by=actor_id,
            env=get_current_env(),
        )

    def cancel(
        self,
        *,
        space_id: int,
        actor_id: str,
        target_type: FavoriteTargetType,
        target_code: str,
    ) -> bool:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        deleted = self._repository.cancel(
            space_id=space_id,
            target_type=target_type,
            target_code=self._target_code(target_code),
            env=get_current_env(),
        )
        if not deleted:
            raise FavoriteNotFoundError("market favorite not found")
        return True

    def search(
        self,
        *,
        space_id: int,
        actor_id: str,
        target_type: FavoriteTargetType | None,
        keyword: str | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[MarketFavoriteRecord]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.search(
            space_id=space_id,
            target_type=target_type,
            keyword=keyword.strip() if keyword and keyword.strip() else None,
            env=get_current_env(),
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )
