"""Persistence contract for the recoverable Offline unit of work."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.space_skill_offline_types import (
        OfflineCommit,
        OfflineInspection,
    )


@runtime_checkable
class SpaceSkillOfflineRepositoryProtocol(Protocol):
    @abstractmethod
    def inspect(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> OfflineInspection: ...

    @abstractmethod
    def commit(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_version_id: int,
        target_version: int,
        new_locator: str,
        new_description: str | None,
        env: str,
        guard: Callable[[OfflineInspection], None],
    ) -> OfflineCommit: ...


__all__ = [
    "SpaceSkillOfflineRepositoryProtocol",
]
