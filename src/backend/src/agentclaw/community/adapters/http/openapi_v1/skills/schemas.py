"""Request/response models for the skills group."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    """One skill owned by a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "skill_id": "sk-2f19",
                "name": "quarterly-report",
                "description": "Drafts the quarterly report from meeting notes.",
                "category": "reporting",
                "tags": ["report", "internal"],
                "active": True,
                "created_at": "2026-07-30T09:00:00+00:00",
                "updated_at": "2026-07-30T09:12:04+00:00",
            }
        }
    )

    skill_id: str = Field(
        description="Identifier of this skill. Use it in the path of the "
        "per-skill endpoints."
    )
    name: str = Field(
        description="Skill name, taken from the uploaded package and unique "
        "within the bot."
    )
    description: str | None = Field(
        default=None, description="What the skill does; null when the package "
        "declares none."
    )
    category: str | None = Field(
        default=None, description="Category the package declares; null when it "
        "declares none."
    )
    tags: list[str] = Field(
        default_factory=list, description="Tags the package declares; empty when "
        "it declares none."
    )
    active: bool = Field(
        description="True when the skill is activated for the bot. An uploaded "
        "skill starts inactive."
    )
    created_at: datetime | str | None = Field(
        default=None, description="When the skill was first uploaded (ISO 8601); "
        "null when not recorded."
    )
    updated_at: datetime | str | None = Field(
        default=None, description="When the skill was last replaced (ISO 8601); "
        "null when not recorded."
    )


class SkillUpload(BaseModel):
    """Outcome of uploading a skill package."""

    operation: Literal["created", "updated"] = Field(
        description="'created' for a new skill, 'updated' when a package of the "
        "same name replaced an existing one."
    )
    skill: Skill = Field(description="The skill as it now stands.")


class SkillState(BaseModel):
    """Outcome of activating or deactivating a skill."""

    skill: Skill = Field(description="The skill as it now stands.")
    changed: bool = Field(
        description="False when the skill was already in the requested state. "
        "The command succeeds either way."
    )
