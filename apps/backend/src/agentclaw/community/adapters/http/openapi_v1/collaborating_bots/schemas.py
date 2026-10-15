"""Response schemas for the collaborating-Bot collection."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CollaborationSummary(BaseModel):
    """The acting user's relationship with one Bot."""

    id: int = Field(description="Primary key of the collaborator relationship.")
    role: Literal["admin", "member"] = Field(
        description="The acting user's role on this Bot."
    )
    joined_at: datetime = Field(description="When the collaboration was created.")


class CollaboratingBotSpace(BaseModel):
    """Business-space reference included with a collaborating Bot."""

    space_id: str = Field(description="Unique identifier of the business space.")
    name: str = Field(description="Display name of the business space.")
    kind: str = Field(description="Space kind from the business-space owner.")


class CollaboratingBot(BaseModel):
    """Bot summary returned to one of its collaborators."""

    bot_id: str = Field(description="Unique identifier of the Bot.")
    bot_name: str = Field(description="Display name of the Bot.")
    bot_desc: str = Field(description="Human-readable description of the Bot.")
    entity_id: str = Field(description="Entity that the Bot record belongs to.")
    owner_id: str = Field(description="User who owns the Bot.")
    engine: str = Field(description="Engine currently selected for the Bot.")
    cluster_name: str = Field(
        description="Public cluster associated with the selected engine."
    )
    bot_type: str = Field(description="Product type of the Bot.")
    status: str = Field(description="Current lifecycle status of the Bot.")
    template_type: str | None = Field(
        default=None,
        description="Template type of the Bot, when configured.",
    )
    template_config: dict | None = Field(
        default=None,
        description=(
            "Stored template snapshot for compatibility with the legacy "
            "collaborator listing. It can contain sensitive creator-supplied "
            "values."
        ),
    )
    space: CollaboratingBotSpace | None = Field(
        default=None,
        description="Business space assigned to the Bot, when resolved.",
    )
    collaboration: CollaborationSummary = Field(
        description="The acting user's collaboration relationship with the Bot."
    )


__all__ = ["CollaboratingBot", "CollaboratingBotSpace", "CollaborationSummary"]
