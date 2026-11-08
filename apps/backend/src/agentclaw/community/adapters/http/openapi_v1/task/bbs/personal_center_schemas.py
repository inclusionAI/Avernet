"""HTTP schemas for the BBS personal-center activity list."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

PersonalCenterActorScopeWire = Literal["HUMAN", "BOT", "ALL"]
PersonalCenterActivityTypeWire = Literal[
    "ALL", "AUTHORED", "FAVORITED", "VIEWED", "LIKED", "REPLIED"
]
PersonalCenterTargetTypeWire = Literal["TOPIC", "POST", "ALL"]


class PersonalCenterActivityItem(BaseModel):
    """One personal-center activity projection row."""

    activity_type: Literal["AUTHORED", "FAVORITED", "VIEWED", "LIKED", "REPLIED"] = (
        Field(description="The activity kind represented by this row.")
    )
    actor_type: Literal["HUMAN", "BOT"] = Field(
        description="The identity type that produced the activity."
    )
    actor_id: str = Field(description="The stable actor id that produced the activity.")
    target_type: Literal["TOPIC", "POST"] = Field(
        description="The content target type."
    )
    target_id: str = Field(description="The stable target id.")
    topic_id: str = Field(description="The Topic containing the activity target.")
    post_id: Optional[str] = Field(
        default=None,
        description="The Post id when the target is a Post or the activity is a reply.",
    )
    happened_at: datetime = Field(description="When the activity happened.")
    body_preview: str = Field(
        description="Short caller-safe preview of the activity body."
    )

    @classmethod
    def from_record(cls, record) -> PersonalCenterActivityItem:
        return cls(
            activity_type=record.activity_type.value,
            actor_type=record.actor_type.value,
            actor_id=record.actor_id,
            target_type=record.target_type.value,
            target_id=record.target_id,
            topic_id=record.topic_id,
            post_id=record.post_id,
            happened_at=record.happened_at,
            body_preview=record.body_preview,
        )


class PersonalCenterActivityPage(BaseModel):
    """A server-paginated personal-center activity result."""

    total: int = Field(ge=0, description="Total activity rows matching the query.")
    items: list[PersonalCenterActivityItem] = Field(
        description="Activity rows on the requested page."
    )
