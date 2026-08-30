"""Service API contract for querying Skills owned by a Space."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.skill_center_types import (
        SpaceSkillSummaryRecord,
        SpaceSkillDetailRecord,
    )


@runtime_checkable
class SpaceSkillQueryServiceProtocol(Protocol):
    """Read-only Space Skill query service."""

    @abstractmethod
    def list_space_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSkillSummaryRecord]]: ...

    @abstractmethod
    def get_space_skill(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillDetailRecord: ...
