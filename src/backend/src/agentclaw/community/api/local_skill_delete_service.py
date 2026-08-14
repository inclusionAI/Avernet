"""Service API for public Bot-owned Local Skill deletion."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LocalSkillDeleteServiceProtocol(Protocol):
    """Delete one inactive Bot-owned Local Skill by deployment-wide ID."""

    async def delete_local_skill(self, *, skill_id: str, actor_id: str) -> None: ...
