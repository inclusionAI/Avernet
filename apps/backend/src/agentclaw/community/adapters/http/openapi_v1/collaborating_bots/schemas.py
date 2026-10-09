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

    bot_id: str
    bot_name: str
    bot_desc: str
    entity_id: str = Field(description="Entity that the Bot record belongs to.")
    owner_id: str = Field(description="User who owns the Bot.")
    engine: str
    cluster_name: str
    bot_type: str
    status: str
    collaboration: CollaborationSummary


__all__ = ["CollaboratingBot", "CollaborationSummary"]
