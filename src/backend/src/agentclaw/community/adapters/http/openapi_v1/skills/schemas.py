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
                # What a first upload through this API records — not a richer
                # pair invented for the example. Other values are possible on a
                # skill that came from elsewhere; these are the common case.
                "category": "general",
                "tags": [],
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
    # Not unique, and the upload service is the proof: `_same_name_matches`
    # collects a list and raises LocalSkillDuplicateError on `len(matches) > 1`,
    # which is a check that only makes sense because duplicates can exist.
    # `ac_skill.name` carries no uniqueness constraint.
    name: str = Field(
        description="Skill name, as recorded for the skill — from the package "
        "for an upload through this API. Not a unique key: nothing enforces "
        "uniqueness within the bot, so a listing can contain two skills with "
        "the same name. Address a skill by `skill_id`."
    )
    description: str | None = Field(
        default=None, description="What the skill does; null when the package "
        "declares none."
    )
    # Both come off the stored record, and nothing on this surface derives them
    # from the package: `LocalSkillUploadService` unpacks only the name and
    # description, hard-codes category "general" and empty tags on a *create*,
    # and on a replace passes neither — `replace_bot_local_skill` takes only the
    # locator and description, so whatever the row held is kept. A skill created
    # through the internal skill-create surface can hold arbitrary values, and
    # this API reads those rows too.
    category: str | None = Field(
        default=None,
        description="Category recorded for the skill. Not read from the "
        "package: a first upload through this API records 'general', and a "
        "re-upload keeps whatever the skill already had. Other values reach "
        "here from skills created outside this API.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags recorded for the skill. Not read from the package: a "
        "first upload through this API records none, and a re-upload keeps "
        "whatever the skill already had. Other values reach here from skills "
        "created outside this API.",
    )
    # Replacement preserves the flag rather than resetting it — the upload
    # service reconciles the runtime in the skill's existing state.
    active: bool = Field(
        description="True when the skill is activated for the bot. A newly "
        "uploaded skill starts inactive; re-uploading over an existing skill "
        "keeps its current state, so replacing an active skill leaves it "
        "active."
    )
    # Both are the record's own timestamps, not package events:
    # `SkillRepository.update` sets gmt_modified for any change it writes —
    # name, description, category, tags, visibility — so a metadata edit moves
    # `updated_at` without the package changing at all.
    created_at: datetime | str | None = Field(
        default=None, description="When the skill record was created (ISO "
        "8601); null when not recorded."
    )
    updated_at: datetime | str | None = Field(
        default=None, description="When the skill record was last changed (ISO "
        "8601); null when not recorded. Any edit moves this, not just replacing "
        "the package — do not read it as the last upload time."
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
