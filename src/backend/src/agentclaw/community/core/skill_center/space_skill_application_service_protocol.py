"""Service API contract for Space Skill Draft application commands."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SpaceSkillCreationOutcome:
    skill_id: int
    created: bool


@runtime_checkable
class SpaceSkillApplicationServiceProtocol(Protocol):
    @abstractmethod
    def create_from_folder(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> SpaceSkillCreationOutcome: ...

    @abstractmethod
    def create_from_git(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        git_url: str,
        branch: str | None,
        subdir: str | None,
    ) -> SpaceSkillCreationOutcome: ...
