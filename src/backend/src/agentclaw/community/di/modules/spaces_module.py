"""DI bindings for spaces, members and market favorites."""

from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceAccessServiceProtocol as SpaceAccessServiceApiProtocol,
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.api.space_skill_query_service import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.core.bot_management.bot_space import (
    BotSpaceAccessProtocol,
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
from agentclaw.community.core.skill_center.services.space_skill_query_service import (
    SpaceSkillQueryService,
)
from agentclaw.community.core.skill_center.services.space_skill_grant_service import (
    SpaceSkillGrantService,
)
from agentclaw.community.core.spaces.protocols import (
    SpaceAccessServiceProtocol as CoreSpaceAccessServiceProtocol,
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
        binder.bind(
            BotSpaceAccessProtocol,
            to=SpaceAccessService,
            scope=singleton,
        )
        binder.bind(
            SpaceAccessServiceApiProtocol,
            to=SpaceAccessService,
            scope=singleton,
        )
        binder.bind(
            CoreSpaceAccessServiceProtocol,
            to=SpaceAccessService,
            scope=singleton,
        )
        binder.bind(SpaceServiceProtocol, to=SpaceService, scope=singleton)
        binder.bind(SpaceMemberServiceProtocol, to=SpaceMemberService, scope=singleton)
        binder.bind(
            SpaceSkillQueryServiceProtocol,
            to=SpaceSkillQueryService,
            scope=singleton,
        )
        binder.bind(
            SpaceSkillGrantServiceProtocol,
            to=SpaceSkillGrantService,
            scope=singleton,
        )
        binder.bind(
            MarketFavoriteServiceProtocol,
            to=MarketFavoriteService,
            scope=singleton,
        )
