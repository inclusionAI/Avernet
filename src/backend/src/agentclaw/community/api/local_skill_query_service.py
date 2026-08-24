"""Service API for public Bot-scoped Skill reads."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LocalSkillQueryServiceProtocol(Protocol):
    """Read desired-state metadata for Skills visible to one actor."""

    @abstractmethod
    def list_bot_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Page every Skill the addressed Bot has, however it reaches it."""
        ...

    @abstractmethod
    def get_local_skill(self, *, skill_id: str, actor_id: str) -> dict[str, Any]: ...
