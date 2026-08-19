"""Request/response models for the skills group.

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    """Public metadata for one Bot-owned Skill."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "skill_id": "42",
                "name": "weekly-report",
                "description": "Builds the weekly status report.",
                "category": "general",
                "tags": [],
                "active": True,
                "created_at": "2026-08-04T10:00:00",
                "updated_at": "2026-08-10T08:30:00",
            }
        }
    )

    skill_id: str = Field(
        description="Identifier of the skill — decimal digits, e.g. '42'. "
        "Stable across package replacement. Address skills by this id, "
        "never by name."
    )
    name: str = Field(
        description="The skill's name, taken from its package manifest — "
        "ASCII letters, digits and hyphens only. Unique per bot, not "
        "globally; renaming means uploading under a new name."
    )
    description: str | None = Field(
        default=None,
        description="What the skill does, from its package manifest.",
    )
    # Pass-through of a free-form column; the public upload always writes
    # "general", other values come from legacy or internal writers.
    category: str | None = Field(
        default=None,
        description="Free-form grouping label. Currently always 'general' "
        "for skills uploaded through this API; other values can appear on "
        "records created elsewhere. Null on some legacy records.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Labels attached to the skill. Currently always empty "
        "for skills uploaded through this API; records created elsewhere "
        "may carry manifest-declared tags.",
    )
    active: bool = Field(
        description="Whether the skill is enabled for the bot. This is the "
        "desired state — it answers even while the bot is offline."
    )
    created_at: datetime | str | None = Field(
        default=None,
        description="When the skill was created (ISO 8601, no timezone "
        "designator); null when unrecorded.",
    )
    updated_at: datetime | str | None = Field(
        default=None,
        description="When the skill last changed (ISO 8601, no timezone "
        "designator); null when unrecorded. Right after a package "
        "replacement this can still show the pre-replacement time — re-read "
        "the skill for the fresh value.",
    )


class SkillUpload(BaseModel):
    """Response for a Skill create or same-name package replacement."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "operation": "created",
                "skill": {
                    "skill_id": "42",
                    "name": "weekly-report",
                    "description": "Builds the weekly status report.",
                    "category": "general",
                    "tags": [],
                    "active": False,
                    "created_at": "2026-08-04T10:00:00",
                    "updated_at": "2026-08-04T10:00:00",
                },
            }
        }
    )

    operation: Literal["created", "updated"] = Field(
        description="'created' — no skill of that name existed for the bot; "
        "a new one was created, inactive, and answers 201. 'updated' — a "
        "same-name skill existed and its package was replaced in place, "
        "keeping its id and active state; answers 200."
    )
    skill: Skill = Field(description="The skill as stored after the upload.")


class SkillState(BaseModel):
    """Result of an idempotent Skill desired-state command."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "skill": {
                    "skill_id": "42",
                    "name": "weekly-report",
                    "description": "Builds the weekly status report.",
                    "category": "general",
                    "tags": [],
                    "active": True,
                    "created_at": "2026-08-04T10:00:00",
                    "updated_at": "2026-08-10T08:30:00",
                },
                "changed": True,
            }
        }
    )

    skill: Skill = Field(description="The skill in its resulting state.")
    changed: bool = Field(
        description="False when the skill was already in the requested state "
        "— the call is idempotent and succeeded either way."
    )


class SkillContent(BaseModel):
    """The consumable, canonical SKILL.md document for one Skill asset."""

    content: str = Field(description="The full SKILL.md document.")


class SkillParameters(BaseModel):
    """The complete Bot-level parameter object for one Skill."""

    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Complete values for this Bot and Skill; replacement is full, not patch semantics.",
    )
