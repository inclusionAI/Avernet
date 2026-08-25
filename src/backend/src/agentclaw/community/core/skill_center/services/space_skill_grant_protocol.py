"""Core-owned Service API Protocol for Space Skill Grant commands."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.skill_center_types import (
        SpaceSkillGrantItem,
    )


@runtime_checkable
class SpaceSkillGrantServiceProtocol(Protocol):
    def list_grants(self, *, space_id: int, skill_id: int, actor_id: str) -> dict: ...

    def add_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
    ) -> SpaceSkillGrantItem: ...

    def remove_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
    ) -> SpaceSkillGrantItem: ...

    def transfer_owner(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        new_owner_user_id: str,
        reason: str | None,
    ) -> dict: ...
