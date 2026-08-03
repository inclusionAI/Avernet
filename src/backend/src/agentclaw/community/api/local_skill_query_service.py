"""Service API for public Bot-scoped Local Skill reads."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LocalSkillQueryServiceProtocol(Protocol):
    """Read desired-state metadata for Local Skills visible to one actor."""

    def list_local_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def get_local_skill(self, *, skill_id: str, actor_id: str) -> dict[str, Any]: ...
