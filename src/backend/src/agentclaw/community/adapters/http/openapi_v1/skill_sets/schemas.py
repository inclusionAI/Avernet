"""Published canonical SkillSet wire models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSetItem(BaseModel):
    """One Bot-scoped SkillSet and its whole-set desired state."""

    id: str = Field(description="Decimal SkillSet identifier.")
    name: str = Field(description="Unique SkillSet name within this Bot.")
    description: str | None = Field(default=None)
    is_default: bool = Field(description="Whether this is the immutable System Default set.")
    is_active: bool = Field(description="Whole-set desired state; ordinary sets never expose partial activation.")


class CreateSkillSetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class UpdateSkillSetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class SkillSetMembershipResult(BaseModel):
    changed: bool = Field(description="False when the requested membership state already existed.")


class SkillSetSkillItem(BaseModel):
    skill_id: str
    name: str
    description: str | None = None


class SkillSetResourceItem(SkillSetItem):
    mcps: list[dict] = Field(default_factory=list)
    clis: list[dict] = Field(default_factory=list)
