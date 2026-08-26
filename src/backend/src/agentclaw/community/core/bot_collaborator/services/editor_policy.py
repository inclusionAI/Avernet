"""Bot and Space authorization policy shared by public Editor operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_collaborator.errors import (
    BotNotFoundError,
    BotNotServiceTypeError,
    CollaboratorNotFoundError,
    CollaboratorSpaceMembershipError,
)
from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol


class EditorPolicy:
    """Resolve and authorize the resources addressed by Editor operations."""

    def __init__(
        self,
        *,
        bot_repo: BotRepository,
        collaborator_repo: CollaboratorRepositoryProtocol,
        space_access_service: SpaceAccessServiceProtocol,
        member_management_capability_service: MemberManagementCapabilityService,
    ) -> None:
        self._bot_repo = bot_repo
        self._collaborator_repo = collaborator_repo
        self._space_access_service = space_access_service
        self._member_management_capability_service = (
            member_management_capability_service
        )

    def resolve_bot(self, *, bot_id: str, owner_id: str) -> dict[str, Any]:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot 不存在: bot_id={bot_id}, owner_id={owner_id}")
        return bot

    def require_capability(self, *, bot: dict[str, Any], bot_id: str) -> None:
        if not self._member_management_capability_service.can_manage_collaborators(
            bot, bot_id
        ):
            raise BotNotServiceTypeError(
                f"Bot 不是服务型且未开启成员管理: bot_id={bot_id}"
            )

    def resolve_record(
        self,
        *,
        bot: dict[str, Any],
        bot_id: str,
        collaborator_id: int,
        env: str,
    ) -> CollaboratorRecord:
        record = self._collaborator_repo.get_by_id(collaborator_id)
        if record is None:
            raise CollaboratorNotFoundError(f"协作者不存在: id={collaborator_id}")
        # COSEC: a public record id is never authority. Bind it back to the
        # addressed Bot, owner and environment before any mutation so an admin
        # cannot operate another Bot's collaborator by guessing an integer id.
        if (
            record.bot_pk != bot["id"]
            or record.bot_id != bot_id
            or record.owner_id != bot["owner_id"]
            or record.env != env
        ):
            raise CollaboratorNotFoundError(f"协作者不存在: id={collaborator_id}")
        return record

    def require_team_space_member(self, *, bot: dict[str, Any], user_id: str) -> None:
        if not self.allows_editor(bot=bot, user_id=user_id):
            # COSEC: editor grants must never outlive or bypass Team Space
            # membership. Unknown references fail closed instead of silently
            # falling back to the legacy unrestricted collaborator behavior.
            raise CollaboratorSpaceMembershipError(
                "editor must be a member of the Bot Team Space"
            )

    def allows_editor(
        self,
        *,
        bot: Mapping[str, Any],
        user_id: str,
        cache: dict[str, bool] | None = None,
    ) -> bool:
        raw_space_id = bot.get("space_id")
        if raw_space_id in (None, "") or str(raw_space_id).startswith("personal:"):
            return True
        key = str(raw_space_id)
        if cache is not None and key in cache:
            return cache[key]
        try:
            space = self._space_access_service.require_space_reference(space_ref=key)
            allowed = space.space_type is not SpaceType.TEAM
            if not allowed:
                self._space_access_service.require_space_member(
                    space_id=space.id, user_id=user_id
                )
                allowed = True
        except (SpaceAccessDeniedError, SpaceNotFoundError, ValueError):
            allowed = False
        if cache is not None:
            cache[key] = allowed
        return allowed


__all__ = ["EditorPolicy"]
