"""DI bindings for spaces, members and market favorites."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

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
from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
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
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
    DraftEditLeaseRepository,
)
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
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.skill_center.services.draft_edit_lease_service import (
    DraftEditLeaseService,
)
from agentclaw.community.core.spaces.protocols import (
    SpaceAccessServiceProtocol as CoreSpaceAccessServiceProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env


class SpacesModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            SpaceSkillEditorRequestServiceProtocol,
            to=SpaceSkillEditorRequestService,
            scope=singleton,
        )
        binder.bind(
            DraftEditLeaseServiceProtocol,
            to=DraftEditLeaseService,
            scope=singleton,
        )
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
            MarketFavoriteServiceProtocol,
            to=MarketFavoriteService,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def space_skill_grant_service(
        self,
        access: CoreSpaceAccessServiceProtocol,
        repository: SpaceSkillRepository,
    ) -> SpaceSkillGrantServiceProtocol:
        """Assemble Grant policy with environment resolution at the DI boundary."""
        return SpaceSkillGrantService(access, repository, get_current_env)

    @singleton
    @provider
    @inject
    def draft_edit_lease_service(
        self,
        access: CoreSpaceAccessServiceProtocol,
        grants: SpaceSkillGrantServiceProtocol,
        repository: DraftEditLeaseRepository,
    ) -> DraftEditLeaseServiceProtocol:
        """Assemble permanent Draft Lease policy at the composition root."""
        return DraftEditLeaseService(access, grants, repository, get_current_env)
