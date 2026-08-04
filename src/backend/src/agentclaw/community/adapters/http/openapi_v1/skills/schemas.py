"""Request/response models for the skills group."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """A skill in the catalog."""

    skill_id: str
    name: str
    description: str | None = None
    category: str | None = None


class SkillDetail(Skill):
    """A skill's full detail."""

    manifest: dict[str, Any] | None = None


class BotSkill(BaseModel):
    """A skill installed on a bot."""

    skill_id: str
    name: str
    enabled: bool


class SkillInstall(BaseModel):
    """Install-a-skill request body."""

    skill_id: str


class LocalSkill(BaseModel):
    """Public metadata for one Bot-owned Local Skill."""

    skill_id: str
    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    active: bool
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class LocalSkillUpload(BaseModel):
    """Response for a Local Skill create or same-name package replacement."""

    operation: Literal["created", "updated"]
    skill: LocalSkill


class LocalSkillState(BaseModel):
    """Result of an idempotent Local Skill desired-state command."""

    skill: LocalSkill
    changed: bool
