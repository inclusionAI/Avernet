"""Request/response models for the skills group."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
