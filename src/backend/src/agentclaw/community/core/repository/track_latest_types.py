"""Persistence projections for Track Latest discovery and Version facts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackLatestCandidate:
    owner_id: str
    bot_id: str


@dataclass(frozen=True, slots=True)
class PublishedTrackLatestVersion:
    skill_version_id: int
    metadata_json: str | None


__all__ = ["PublishedTrackLatestVersion", "TrackLatestCandidate"]
