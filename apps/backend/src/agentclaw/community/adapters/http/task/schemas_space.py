"""Task-facing Space list DTOs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaskSpaceItem(BaseModel):
    """Minimal Space identity needed by the Todo workspace selector."""

    space_id: int = Field(description="Unique numeric Space identifier.")
    space_name: str = Field(description="Display name of the Space.")
    space_type: Literal["PERSONAL", "TEAM"] = Field(
        description="Whether the Space is personal or shared by a team."
    )
