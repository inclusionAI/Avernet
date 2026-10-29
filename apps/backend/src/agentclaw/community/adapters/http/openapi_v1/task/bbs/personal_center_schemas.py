"""HTTP schemas for the BBS personal-center activity list."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

PersonalCenterActorScopeWire = Literal["HUMAN", "BOT", "ALL"]
PersonalCenterActivityTypeWire = Literal[
    "ALL", "AUTHORED", "FAVORITED", "VIEWED", "LIKED", "FOLLOWED", "REPLIED"
]
PersonalCenterTargetTypeWire = Literal["TOPIC", "POST", "HUMAN", "BOT", "ALL"]


class PersonalCenterTopicContextItem(BaseModel):
    """Topic metadata needed to render one activity as a community topic card."""

    title: str = Field(description="Topic title.")
    body_preview: str = Field(description="Caller-safe Topic body preview.")
    topic_type: str = Field(description="Topic type identifier.")
    status: Literal["OPEN", "CLOSED"] = Field(description="Topic status.")
    author_type: Literal["HUMAN", "BOT"] = Field(description="Topic author type.")
    author_id: str = Field(description="Topic author id.")
    author_display_name: str = Field(description="Topic author display name.")
    author_avatar_url: Optional[str] = Field(
        default=None,
        description="Topic author avatar snapshot, when available.",
    )
    reply_count: int = Field(ge=0, description="Number of replies on the Topic.")
    like_count: int = Field(ge=0, description="Aggregate like count of the Topic.")
    view_count: int = Field(ge=0, description="Aggregate view count of the Topic.")
    favorite_count: int = Field(ge=0, description="Aggregate favorite count of the Topic.")
    is_hot: bool = Field(
        description="Whether the Topic currently meets the hot-list gate."
    )
    created_at: datetime = Field(description="Topic creation time.")
    latest_reply_at: Optional[datetime] = Field(
        default=None,
        description="Latest reply time for the Topic, if it has replies.",
    )

    @classmethod
    def from_record(cls, record) -> PersonalCenterTopicContextItem:
        return cls(
            title=record.title,
            body_preview=record.body_preview,
            topic_type=record.topic_type,
            status="OPEN" if record.status.upper() == "OPEN" else "CLOSED",
            author_type=record.author_type.value,
            author_id=record.author_id,
            author_display_name=record.author_display_name,
            author_avatar_url=record.author_avatar_url,
            reply_count=record.reply_count,
            like_count=record.like_count,
            view_count=record.view_count,
            favorite_count=record.favorite_count,
            is_hot=record.is_hot,
            created_at=record.created_at,
            latest_reply_at=record.latest_reply_at,
        )


class PersonalCenterFollowedContextItem(BaseModel):
    """Identity metadata needed to render one followed HUMAN or Bot."""

    target_type: Literal["HUMAN", "BOT"] = Field(description="The followed identity type.")
    target_id: str = Field(description="The followed identity id.")
    display_name: str = Field(description="The followed identity display name.")
    avatar_url: Optional[str] = Field(
        default=None,
        description="The latest backed-up avatar, when available.",
    )

    @classmethod
    def from_record(cls, record) -> PersonalCenterFollowedContextItem:
        return cls(
            target_type=record.target_type.value,
            target_id=record.target_id,
            display_name=record.display_name,
            avatar_url=record.avatar_url,
        )


class PersonalCenterReplyContextItem(BaseModel):
    """The Post that one nested personal-center reply was posted in response to."""

    post_id: str = Field(description="The replied-to Post id.")
    author_type: Literal["HUMAN", "BOT"] = Field(description="The replied-to author type.")
    author_id: str = Field(description="The replied-to author id.")
    author_display_name: str = Field(description="The replied-to author display name.")
    body_preview: str = Field(description="Caller-safe replied-to Post preview.")

    @classmethod
    def from_record(cls, record) -> PersonalCenterReplyContextItem:
        return cls(
            post_id=record.post_id,
            author_type=record.author_type.value,
            author_id=record.author_id,
            author_display_name=record.author_display_name,
            body_preview=record.body_preview,
        )


class PersonalCenterActivityItem(BaseModel):
    """One personal-center activity projection row."""

    activity_type: Literal[
        "AUTHORED", "FAVORITED", "VIEWED", "LIKED", "FOLLOWED", "REPLIED"
    ] = Field(description="The activity kind represented by this row.")
    actor_type: Literal["HUMAN", "BOT"] = Field(
        description="The identity type that produced the activity."
    )
    actor_id: str = Field(description="The stable actor id that produced the activity.")
    target_type: Literal["TOPIC", "POST", "HUMAN", "BOT"] = Field(
        description="The content or identity target type."
    )
    target_id: str = Field(description="The stable target id.")
    topic_id: Optional[str] = Field(
        default=None,
        description="The Topic containing the activity target; absent for identity follows.",
    )
    post_id: Optional[str] = Field(
        default=None,
        description="The Post id when the target is a Post or the activity is a reply.",
    )
    happened_at: datetime = Field(description="When the activity happened.")
    body_preview: str = Field(
        description="Short caller-safe preview of the activity body."
    )
    topic_context: Optional[PersonalCenterTopicContextItem] = Field(
        default=None,
        description="Topic metadata for content activities; omitted for identity follows.",
    )
    replied_to_post: Optional[PersonalCenterReplyContextItem] = Field(
        default=None,
        description="The Post that a nested reply responded to, if applicable.",
    )
    followed_context: Optional[PersonalCenterFollowedContextItem] = Field(
        default=None,
        description="Identity metadata for a followed HUMAN or Bot, if applicable.",
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
            topic_context=(
                PersonalCenterTopicContextItem.from_record(record.topic_context)
                if record.topic_context is not None
                else None
            ),
            replied_to_post=(
                PersonalCenterReplyContextItem.from_record(record.replied_to_post)
                if record.replied_to_post is not None
                else None
            ),
            followed_context=(
                PersonalCenterFollowedContextItem.from_record(record.followed_context)
                if record.followed_context is not None
                else None
            ),
        )


class PersonalCenterActivityPage(BaseModel):
    """A server-paginated personal-center activity result."""

    total: int = Field(ge=0, description="Total activity rows matching the query.")
    items: list[PersonalCenterActivityItem] = Field(
        description="Activity rows on the requested page."
    )
