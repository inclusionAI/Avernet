"""Persistence contract for Track Latest candidate and Version fact reads."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.track_latest_types import (
        PublishedTrackLatestVersion,
        TrackLatestCandidateFacts,
    )


@runtime_checkable
class TrackLatestRepositoryProtocol(Protocol):
    @abstractmethod
    def list_candidate_facts(
        self, *, env: str, skill_id: int
    ) -> TrackLatestCandidateFacts: ...

    @abstractmethod
    def list_published_versions(
        self, *, env: str, skill_id: int
    ) -> tuple[PublishedTrackLatestVersion, ...]: ...


__all__ = [
    "TrackLatestRepositoryProtocol",
]
