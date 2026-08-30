"""Exact-version SC Public synchronization result contract."""

from __future__ import annotations

from dataclasses import dataclass


class SkillCenterSyncInProgressError(RuntimeError):
    """Another manual or periodic exact sync currently owns the environment."""


@dataclass(frozen=True, slots=True)
class SkillCenterSyncFailure:
    skill_id: str
    skill_code: str
    error_code: str


@dataclass(frozen=True, slots=True)
class SkillCenterSyncSummary:
    scanned: int
    updated: int
    unchanged: int
    failed: int
    failures: tuple[SkillCenterSyncFailure, ...]


__all__ = [
    "SkillCenterSyncFailure",
    "SkillCenterSyncInProgressError",
    "SkillCenterSyncSummary",
]
