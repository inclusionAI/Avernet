"""Persistence projections for Track Latest discovery and dependency deltas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackLatestCandidate:
    owner_id: str
    bot_id: str


@dataclass(frozen=True, slots=True)
class TrackLatestDependencyDelta:
    skill_version_id: int
    claimed_mcp: frozenset[str]
    released_mcp: frozenset[str]


__all__ = ["TrackLatestCandidate", "TrackLatestDependencyDelta"]
