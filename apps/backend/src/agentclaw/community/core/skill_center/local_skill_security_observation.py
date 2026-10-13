"""Public facts for Local Skill security identity and observation history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class LocalSkillSecurityIdentity:
    provider_skill_id: str
    skill_id: int | None
    incarnation_id: str
    env: str
    bot_id: str
    owner_id: str
    creator_id: str | None
    current_observation_id: str | None
    sync_target_observation_id: str | None
    sync_completed_observation_id: str | None
    sync_status: str
    sync_error_code: str | None
    sync_error_message: str | None
    created_at: datetime


@dataclass(frozen=True)
class LocalSkillSecurityObservation:
    observation_id: str
    upload_order: int
    provider_skill_id: str
    skill_id: int | None
    incarnation_id: str
    env: str
    bot_id: str
    owner_id: str
    modifier_id: str
    name: str
    description: str
    sha256: str
    snapshot_ref: str
    asset_deleted: bool
    external_artifact: dict[str, Any] | None
    check_id: str | None
    processing_status: str
    risk_status: str | None
    report: dict[str, Any] | None
    detail_url: str | None
    error_code: str | None
    error_message: str | None
    scan_started_at: datetime | None
    scan_completed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class LocalSkillSecurityHandoffAttempt:
    observation_id: str
    upload_order: int
    order_known: bool
    skill_id: int
    incarnation_id: str
    owner_id: str
    bot_id: str
    actor_id: str
    name: str | None
    description: str | None
    creator_id: str | None
    sha256: str
    snapshot_ref: str
    stage: str
    snapshot_verified: bool
    env: str


__all__ = [
    "LocalSkillSecurityIdentity",
    "LocalSkillSecurityObservation",
    "LocalSkillSecurityHandoffAttempt",
]
