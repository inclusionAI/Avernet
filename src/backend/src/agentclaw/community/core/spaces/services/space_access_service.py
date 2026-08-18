"""Central authorization rules for space-scoped operations."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.utils.env_utils import get_current_env


class SpaceAccessService:
    @inject
    def __init__(self, repository: SpaceRepositoryProtocol) -> None:
        self._repository = repository

    def require_space(self, *, space_id: int):
        space = self._repository.get_space(space_id=space_id, env=get_current_env())
        if space is None:
            raise SpaceNotFoundError("space not found")
        return space

    def get_space_role(self, *, space_id: int, user_id: str) -> SpaceRole | None:
        member = self._repository.get_member(
            space_id=space_id, user_id=user_id, env=get_current_env()
        )
        return member.role if member is not None else None

    def require_space_member(self, *, space_id: int, user_id: str):
        space = self.require_space(space_id=space_id)
        member = self._repository.get_member(
            space_id=space_id, user_id=user_id, env=get_current_env()
        )
        if member is None:
            raise SpaceAccessDeniedError("space membership required")
        return space, member

    def require_space_owner(self, *, space_id: int, user_id: str):
        space, member = self.require_space_member(space_id=space_id, user_id=user_id)
        if member.role is not SpaceRole.OWNER:
            raise SpaceAccessDeniedError("space owner role required")
        return space, member

    def require_space_creator(self, *, space_id: int, user_id: str):
        space = self.require_space(space_id=space_id)
        if space.created_by != user_id:
            raise SpaceAccessDeniedError("space creator required")
        return space
