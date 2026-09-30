"""Raw persistence projections for Track Latest discovery and Version facts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackLatestCandidate:
    owner_id: str
    bot_id: str


@dataclass(frozen=True, slots=True)
class TrackLatestInstallationFact:
    owner_id: str
    bot_id: str


@dataclass(frozen=True, slots=True)
class TrackLatestSkillSetFact:
    owner_id: str | None
    bot_id: str | None
    engine_type: str | None
    is_default: bool
    is_active: bool


@dataclass(frozen=True, slots=True)
class TrackLatestBotFact:
    owner_id: str
    bot_id: str
    active_engine: str
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class TrackLatestCandidateFacts:
    installations: tuple[TrackLatestInstallationFact, ...]
    skill_sets: tuple[TrackLatestSkillSetFact, ...]
    bots: tuple[TrackLatestBotFact, ...]


@dataclass(frozen=True, slots=True)
class PublishedTrackLatestVersion:
    skill_version_id: int
    metadata_json: str | None


__all__ = [
    "PublishedTrackLatestVersion",
    "TrackLatestBotFact",
    "TrackLatestCandidate",
    "TrackLatestCandidateFacts",
    "TrackLatestInstallationFact",
    "TrackLatestSkillSetFact",
]
