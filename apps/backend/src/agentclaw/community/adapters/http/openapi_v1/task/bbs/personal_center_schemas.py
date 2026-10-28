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
    activity_type: Literal["AUTHORED", "FAVORITED", "VIEWED", "LIKED", "REPLIED"]
    actor_type: Literal["HUMAN", "BOT"]
    actor_id: str
    target_type: Literal["TOPIC", "POST"]
    target_id: str
    topic_id: str
    post_id: Optional[str] = None
    happened_at: datetime
    body_preview: str

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
    total: int = Field(ge=0)
    items: list[PersonalCenterActivityItem]
