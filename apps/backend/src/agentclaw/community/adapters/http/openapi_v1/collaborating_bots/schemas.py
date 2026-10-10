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


class CollaboratingBot(BaseModel):
    """Safe Bot summary returned to one of its collaborators."""

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
    collaboration: CollaborationSummary = Field(
        description="The acting user's collaboration relationship with the Bot."
    )


__all__ = ["CollaboratingBot", "CollaborationSummary"]
