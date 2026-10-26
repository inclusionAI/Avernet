"""Read-only expert discovery payloads."""

from pydantic import BaseModel, Field


class ExpertSummary(BaseModel):
    """A discoverable expert template and its historical creation count."""

    expert_id: str = Field(description="Stable expert identifier.")
    name: str = Field(description="Expert display name.")
    description: str = Field(description="Expert responsibilities.")
    category: str = Field(description="Expert category.")
    tags: list[str] = Field(description="Author-provided search tags.")
    creation_count: int = Field(
        description="Historical number of independently created Bots, including deleted Bots."
    )
    first_added_at: str = Field(
        description="First successful catalogue registration time."
    )


class ExpertIdentity(BaseModel):
    """Declared identity text presented as safe Markdown."""

    type: str = Field(description="Declared identity filename.")
    content: str = Field(description="Complete identity Markdown text.")


class ExpertSkill(BaseModel):
    """Metadata read from a validated standard Skill package."""

    name: str = Field(description="Skill runtime name.")
    description: str = Field(description="Skill responsibilities.")


class ExpertDetail(ExpertSummary):
    """Expert display data without execution documents or source credentials."""

    identity: list[ExpertIdentity] = Field(
        description="Declared identity files in author order."
    )
    skills: list[ExpertSkill] = Field(description="Declared local Skill metadata.")
