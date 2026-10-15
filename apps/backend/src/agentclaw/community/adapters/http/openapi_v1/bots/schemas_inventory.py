"""Public publication summary for an inventory card."""

from pydantic import BaseModel, Field


class InventoryPublication(BaseModel):
    id: int = Field(description="Publication id represented by this service card.")
    status: str = Field(
        description="Stored publication status, as in the legacy publish.status; "
        "not the Bot status or a normalized display state."
    )
    version: int | None = Field(description="Version of this publication, when assigned.")
