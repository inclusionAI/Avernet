"""Team Space deletion service."""

from __future__ import annotations

from collections.abc import Callable

from injector import inject

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.notifications import (
    notify_space_manager_sync,
)
from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import PersonalSpaceDeletionError
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.spaces.space_service_protocol import (
    SpaceDeletionServiceProtocol,
)
from agentclaw.community.kernel.bot_manager_sync import SpaceChange
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class SpaceDeletionService(SpaceDeletionServiceProtocol):
    @inject
    def __init__(
        self,
        repository: SpaceRepositoryProtocol,
        access: SpaceAccessService,
        manager_sync: BotManagerSyncProtocol,
        env_provider: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._access = access
        self._manager_sync = manager_sync
        self._env_provider = env_provider

    def delete_team_space(self, *, space_id: int, actor_id: str) -> None:
        env = self._env_provider()
        space = self._access.require_space(space_id=space_id, env=env)
        if space.space_type is SpaceType.PERSONAL:
            raise PersonalSpaceDeletionError("personal space cannot be deleted")
        # Capture the space references before the soft-delete commits: the fan
        # out must still be able to address the now-deleted Space.
        refs = (str(space.id), f"team:{space.id}", space.space_code)
        self._repository.delete_team_space(
            space_id=space_id, actor_id=actor_id, env=env
        )
        # Post-commit best-effort BCS hint; failures are logged inside and
        # never change the committed business result.
        notify_space_manager_sync(
            self._manager_sync,
            change=SpaceChange(
                avernet_tenant=get_current_avernet_tenant(),
                env=env,
                space_refs=refs,
                user_ids=(),
            ),
            space_id=space_id,
        )
