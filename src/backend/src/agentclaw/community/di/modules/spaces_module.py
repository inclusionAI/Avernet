"""DI bindings for spaces, members and market favorites."""

from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.market_favorites.services import MarketFavoriteService
from agentclaw.community.core.repository.implementations.market_favorites import (
    MarketFavoriteRepository,
)
from agentclaw.community.core.repository.implementations.spaces import SpaceRepository
from agentclaw.community.core.repository.protocols.market_favorites import (
    MarketFavoriteRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.services import (
    SpaceAccessService,
    SpaceMemberService,
    SpaceService,
)


class SpacesModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(SpaceRepositoryProtocol, to=SpaceRepository, scope=singleton)
        binder.bind(
            MarketFavoriteRepositoryProtocol,
            to=MarketFavoriteRepository,
            scope=singleton,
        )
        binder.bind(SpaceAccessService, to=SpaceAccessService, scope=singleton)
        binder.bind(SpaceServiceProtocol, to=SpaceService, scope=singleton)
        binder.bind(SpaceMemberServiceProtocol, to=SpaceMemberService, scope=singleton)
        binder.bind(
            MarketFavoriteServiceProtocol,
            to=MarketFavoriteService,
            scope=singleton,
        )
