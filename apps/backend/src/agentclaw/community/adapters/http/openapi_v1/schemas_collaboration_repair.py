"""Published report schema for the collaboration repair endpoint.

The field names and the fixed component/status vocabularies mirror Task 4's
kernel contract (``kernel/collaboration_repair.py``) exactly. The Literal
aliases are re-declared here rather than imported so the generated OpenAPI
document publishes each finite vocabulary as an enum, and so this schema stays
a transport contract rather than a re-export of core types.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Component = Literal["ownership", "profile", "managers"]
ItemStatus = Literal[
    "succeeded", "failed", "unknown", "skipped_dependency"
]
ReportStatus = Literal["succeeded", "partial_failed", "failed"]


class CollaborationRepairItem(BaseModel):
    """One component's outcome. An unconfirmed outcome is never success."""

    model_config = ConfigDict(extra="forbid")

    component: Component = Field(description="The repaired component.")
    status: ItemStatus = Field(description="The component's terminal outcome.")
    error_code: str | None = Field(
        default=None,
        description="Stable machine reason for a failed/unknown item; null otherwise.",
    )


class CollaborationRepairReport(BaseModel):
    """The three-item report; its status aggregates the item statuses."""

    model_config = ConfigDict(extra="forbid")

    bot_id: str = Field(description="The repaired Bot's Backend id.")
    status: ReportStatus = Field(
        description="Aggregate outcome derived from the three items."
    )
    items: list[CollaborationRepairItem] = Field(
        min_length=3,
        max_length=3,
        description=(
            "Exactly three items in the fixed order: ownership, profile, "
            "managers."
        ),
    )


__all__ = [
    "CollaborationRepairItem",
    "CollaborationRepairReport",
    "Component",
    "ItemStatus",
    "ReportStatus",
]
