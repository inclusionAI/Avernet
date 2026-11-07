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


class ExpertMcp(BaseModel):
    """A template declaration, without connection overrides or authorization claims."""

    server_code: str = Field(
        description="Declared Registry MCP code; not an authorization or runtime status."
    )


class ExpertResource(BaseModel):
    """A resource destination without source locations or file content."""

    path: str = Field(description="Workspace-relative destination; directory paths end in /.")


class ExpertDetail(ExpertSummary):
    """Expert display data without execution documents or source credentials."""

    identity: list[ExpertIdentity] = Field(
        description="Declared identity files in author order."
    )
    skills: list[ExpertSkill] = Field(description="Declared local Skill metadata.")
    mcp: list[ExpertMcp] = Field(
        default_factory=list,
        description="Declared MCP servers; connection overrides are omitted.",
    )
    resources: list[ExpertResource] | None = Field(
        default=None,
        description=(
            "Declared workspace-relative resource destinations, without source or content. "
            "Null means omitted/no operation; [] clears non-protected workspace items; "
            "a nonempty list clears non-protected items before writing these resources."
        ),
    )
