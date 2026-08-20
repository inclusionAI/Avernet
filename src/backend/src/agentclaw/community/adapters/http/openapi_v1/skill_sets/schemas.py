"""Published canonical SkillSet wire models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSetItem(BaseModel):
    """One Bot-scoped SkillSet and its whole-set desired state."""

    id: str = Field(description="Decimal SkillSet identifier.")
    name: str = Field(description="Unique SkillSet name within this Bot.")
    description: str | None = Field(default=None, description="Optional SkillSet description.")
    is_default: bool = Field(description="Whether this is the immutable System Default set.")
    is_active: bool = Field(description="Whole-set desired state; ordinary sets never expose partial activation.")


class CreateSkillSetRequest(BaseModel):
    """Create an inactive ordinary SkillSet for the addressed Bot."""

    name: str = Field(min_length=1, max_length=100, description="Unique SkillSet name within the Bot.")
    description: str | None = Field(default=None, description="Optional SkillSet description.")


class UpdateSkillSetRequest(BaseModel):
    """Mutable fields of an ordinary SkillSet."""

    name: str | None = Field(default=None, min_length=1, max_length=100, description="Replacement SkillSet name.")
    description: str | None = Field(default=None, description="Replacement SkillSet description.")


class SkillSetMembershipResult(BaseModel):
    """Result of an idempotent SkillSet membership command."""

    changed: bool = Field(description="False when the requested membership state already existed.")


class SkillSetSkillItem(BaseModel):
    """A Skill listed as a member of a SkillSet."""

    skill_id: str = Field(description="Stable decimal Skill identifier.")
    name: str = Field(description="Skill display name.")
    description: str | None = Field(default=None, description="Optional Skill description.")


class SkillSetResourceItem(SkillSetItem):
    """Read-only resource projection for a SkillSet."""

    mcps: list[dict] = Field(default_factory=list, description="MCP resources associated with the set.")
    clis: list[dict] = Field(default_factory=list, description="CLI resources projected only for System Default.")
