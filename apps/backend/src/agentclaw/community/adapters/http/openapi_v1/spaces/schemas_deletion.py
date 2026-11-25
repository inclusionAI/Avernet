"""Deletion response models for the public spaces surface."""

from pydantic import BaseModel, Field


class SpaceDeletedResult(BaseModel):
    """The confirmed soft-deletion of one Team Space."""

    space_id: int = Field(
        ge=1, description="Space primary identifier that was deleted."
    )
    deleted: bool = Field(default=True, description="Whether the deletion succeeded.")
