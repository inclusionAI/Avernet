"""Central authorization rules for space-scoped operations."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceRole, SpaceType
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

    def require_space_reference(self, *, space_ref: str):
        """Resolve the structured Bot ``space_id`` by numeric id or Space code."""
        normalized = space_ref.strip()
        team_reference = normalized.startswith("team:")
        if team_reference:
            normalized = normalized.removeprefix("team:")
        if normalized.isdigit():
            space = self.require_space(space_id=int(normalized))
        else:
            space = self._repository.get_space_by_code(
                space_code=normalized, env=get_current_env()
            )
        if space is None:
            raise SpaceNotFoundError("space not found")
        # COSEC: the legacy ``team:`` prefix is a type assertion, not decoration.
        # Refuse a mismatched Personal Space instead of weakening Team membership
        # enforcement through a malformed persisted reference.
        if team_reference and space.space_type is not SpaceType.TEAM:
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

    def require_space_admin(self, *, space_id: int, user_id: str):
        space = self.require_space(space_id=space_id)
        if space.created_by == user_id:
            member = self._repository.get_member(
                space_id=space_id, user_id=user_id, env=get_current_env()
            )
            return space, member
        member = self._repository.get_member(
            space_id=space_id, user_id=user_id, env=get_current_env()
        )
        if member is None or member.role not in (
            SpaceRole.ADMIN, SpaceRole.OWNER, SpaceRole.ADMINISTRATOR
        ):
            raise SpaceAccessDeniedError("space owner role required")
        return space, member

    def require_space_owner(self, *, space_id: int, user_id: str):
        """Compatibility wrapper; membership administration now requires ADMIN."""
        return self.require_space_admin(space_id=space_id, user_id=user_id)

    def require_space_creator(self, *, space_id: int, user_id: str):
        space = self.require_space(space_id=space_id)
        if space.created_by != user_id:
            raise SpaceAccessDeniedError("space creator required")
        return space
