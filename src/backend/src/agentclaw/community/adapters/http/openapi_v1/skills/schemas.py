"""Request/response models for the skills group."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """Public metadata for one Bot-owned Skill."""

    skill_id: str
    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    active: bool
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class SkillUpload(BaseModel):
    """Response for a first-time Skill upload."""

    operation: Literal["created"]
    skill: Skill


class SkillState(BaseModel):
    """Result of an idempotent Skill desired-state command."""

    skill: Skill
    changed: bool
