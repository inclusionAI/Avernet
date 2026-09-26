"""Persistence projections shared by the SC Public Reference repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentclaw.community.core.skill_center.reference_contract import (
    SkillCenterReferenceStatus,
)


@dataclass(frozen=True, slots=True)
class SkillCenterReferenceWorkItem:
    reference_id: str
    skill_code: str
    status: SkillCenterReferenceStatus
    sc_version_number: str | None
    skill_version_id: int | None
    resolved_skill_id: int | None
    attempt_count: int
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SkillCenterReferenceWorkBatch:
    request_id: str
    env: str
    bot_id: str
    owner_id: str
    skill_set_id: str
    actor_id: str
    items: tuple[SkillCenterReferenceWorkItem, ...]


@dataclass(frozen=True, slots=True)
class PublicCenterVersionTarget:
    skill_id: int
    skill_version_id: int
    status: Literal["MATERIALIZING", "PUBLISHED"]


@dataclass(frozen=True, slots=True)
class MaterializedPublicCenterAsset:
    skill_id: int
    skill_code: str
    name: str
    description: str | None


__all__ = [
    "MaterializedPublicCenterAsset",
    "PublicCenterVersionTarget",
    "SkillCenterReferenceWorkBatch",
    "SkillCenterReferenceWorkItem",
]
