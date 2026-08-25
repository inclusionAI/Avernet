"""Public re-export of the core-owned Space Skill Grant Service API."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.skill_center_types import (
        SpaceSkillGrantItem,
        SpaceSkillGrantViewRecord,
    )


@runtime_checkable
class SpaceSkillGrantServiceProtocol(Protocol):
    """Service API for active OWNER/MANAGER Grant commands."""

    def list_grants(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillGrantViewRecord:
        """Return active Grants and the actor's ACL/Grant qualifications."""
        ...

    def add_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
    ) -> SpaceSkillGrantItem:
        """Idempotently grant MANAGER to an active Space Member."""
        ...

    def remove_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
    ) -> SpaceSkillGrantItem:
        """Idempotently revoke the addressed MANAGER Grant."""
        ...

    def transfer_owner(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        new_owner_user_id: str,
        reason: str | None,
    ) -> SpaceSkillGrantViewRecord:
        """Atomically move the unique OWNER slot and return the new view."""
        ...


__all__ = ["SpaceSkillGrantServiceProtocol"]
