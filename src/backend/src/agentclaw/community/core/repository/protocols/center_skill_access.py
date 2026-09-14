"""Persistence facts needed to authorize one governed Center Skill."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from .skill_center_types import CenterSkillAccessRecord


@runtime_checkable
class CenterSkillAccessRepositoryProtocol(Protocol):
    """Read ownership facts without deciding actor access or Version choice."""

    @abstractmethod
    def get_access(
        self, *, env: str, skill_id: int
    ) -> CenterSkillAccessRecord | None: ...
