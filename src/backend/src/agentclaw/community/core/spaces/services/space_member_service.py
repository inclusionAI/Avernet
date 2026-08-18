"""Space member management service."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import (
    PersonalSpaceInvariantError,
    SpaceCreatorInvariantError,
    SpaceMemberAlreadyExistsError,
    SpaceMemberInvalidError,
    SpaceMemberNotFoundError,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceMemberSummaryRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.utils.env_utils import get_current_env


class SpaceMemberService:
    @staticmethod
    def _user_id(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise SpaceMemberInvalidError("member user id is empty")
        return normalized

    @inject
    def __init__(
        self,
        repository: SpaceRepositoryProtocol,
        access: SpaceAccessService,
    ) -> None:
        self._repository = repository
        self._access = access

    def list_members(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceMemberSummaryRecord]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.list_members(
            space_id=space_id,
            env=get_current_env(),
            keyword=keyword.strip() if keyword and keyword.strip() else None,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )

    def add_member(
        self,
        *,
        space_id: int,
        actor_id: str,
        user_id: str,
        role: SpaceRole,
    ) -> SpaceMemberRecord:
        space, _ = self._access.require_space_owner(space_id=space_id, user_id=actor_id)
        if space.space_type is SpaceType.PERSONAL:
            raise PersonalSpaceInvariantError("personal space cannot add members")
        normalized = self._user_id(user_id)
        if (
            self._repository.get_member(
                space_id=space_id, user_id=normalized, env=get_current_env()
            )
            is not None
        ):
            raise SpaceMemberAlreadyExistsError("space member already exists")
        return self._repository.add_member(
            space_id=space_id,
            user_id=normalized,
            role=role,
            creator_id=actor_id,
            env=get_current_env(),
        )

    def delete_member(self, *, space_id: int, actor_id: str, user_id: str) -> bool:
        space, _ = self._access.require_space_owner(space_id=space_id, user_id=actor_id)
        normalized = self._user_id(user_id)
        if normalized == space.created_by:
            raise SpaceCreatorInvariantError("space creator cannot be removed")
        deleted = self._repository.delete_member(
            space_id=space_id, user_id=normalized, env=get_current_env()
        )
        if not deleted:
            raise SpaceMemberNotFoundError("space member not found")
        return True

    def update_role(
        self, *, space_id: int, actor_id: str, user_id: str, role: SpaceRole
    ) -> SpaceMemberSummaryRecord:
        space, _ = self._access.require_space_owner(space_id=space_id, user_id=actor_id)
        if space.space_type is SpaceType.PERSONAL:
            raise PersonalSpaceInvariantError("personal space role is immutable")
        normalized = self._user_id(user_id)
        if normalized == space.created_by and role is not SpaceRole.OWNER:
            raise SpaceCreatorInvariantError("space creator cannot be demoted")
        updated = self._repository.update_member_role(
            space_id=space_id,
            user_id=normalized,
            role=role,
            env=get_current_env(),
        )
        if updated is None:
            raise SpaceMemberNotFoundError("space member not found")
        return SpaceMemberSummaryRecord(
            member=updated, is_creator=updated.user_id == space.created_by
        )
