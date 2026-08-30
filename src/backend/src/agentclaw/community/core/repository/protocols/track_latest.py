"""Persistence contract for Track Latest candidate and Version-delta reads."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.track_latest_types import (
        TrackLatestCandidate,
        TrackLatestDependencyDelta,
    )


@runtime_checkable
class TrackLatestRepositoryProtocol(Protocol):
    @abstractmethod
    def list_candidates(
        self, *, env: str, skill_id: int
    ) -> tuple[TrackLatestCandidate, ...]: ...

    @abstractmethod
    def latest_dependency_delta(
        self, *, env: str, skill_id: int
    ) -> TrackLatestDependencyDelta: ...


__all__ = [
    "TrackLatestRepositoryProtocol",
]
