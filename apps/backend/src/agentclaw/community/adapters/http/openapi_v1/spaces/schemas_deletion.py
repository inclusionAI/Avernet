"""Deletion response models for the public spaces surface."""

from pydantic import BaseModel, Field


class SpaceDeletedResult(BaseModel):
    space_id: int = Field(
        ge=1, description="Space primary identifier that was deleted."
    )
    deleted: bool = Field(default=True, description="Whether the deletion succeeded.")
