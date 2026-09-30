"""Read-only persistence contract for published Space Skill Versions."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from .skill_center_types import (
    ConsumableSpaceSkillRecord,
    SpaceSkillVersionRecord,
)


@runtime_checkable
class SpaceSkillVersionReadRepository(Protocol):
    @abstractmethod
    def list_published(
        self, *, space_id: int, skill_id: int, env: str, offset: int, limit: int
    ) -> tuple[int, list[SpaceSkillVersionRecord]]: ...

    @abstractmethod
    def get_published_ordinal(
        self, *, space_id: int, skill_id: int, version: int, env: str
    ) -> SpaceSkillVersionRecord: ...

    @abstractmethod
    def list_consumable_candidates(
        self,
        *,
        space_id: int,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[ConsumableSpaceSkillRecord]]: ...
