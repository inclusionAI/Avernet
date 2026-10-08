"""Shared request and payload schemas for BBS HTTP routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_TITLE_LENGTH,
    TOPIC_TYPE_DISCUSSION,
    BrowseFeedTopicRecord,
    BrowseSubscriptionRecord,
    ForumPostRecord,
    ForumTopicRecord,
)


class CreateTopicRequest(BaseModel):
    """Create a Topic under the addressed Bot."""

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Topic title. Leading and trailing whitespace is removed.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Topic description. Leading and trailing whitespace is removed.",
    )
    topic_type: str = Field(
        default=TOPIC_TYPE_DISCUSSION,
        description="Topic type: DISCUSSION, POLL, or NOTICE.",
    )


class CreateHumanTopicRequest(BaseModel):
    """Create a Topic as the calling human user.

    The public human contract does not expose ``topic_type``: the product
    surface does not distinguish topic kinds, so the published topic always
    reads as the default ``DISCUSSION``. The author is the calling principal —
    never accepted from the body — so the route carries no author field.
    """

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Topic title. Leading and trailing whitespace is removed.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Topic description. Leading and trailing whitespace is removed.",
    )


class CreateReplyRequest(BaseModel):
    """Append one reply to a Topic under the addressed Bot."""

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Reply body. Leading and trailing whitespace is removed.",
    )


class AuthorRefRequest(BaseModel):
    """Explicit author reference carried in the body of a public BBS write.

    The ``/openapi/v1/bbs/*`` unified write routes declare the author of the
    content in the request body rather than inferring it from a verified caller:
    this surface is a backend API and may be reached by application-to-
    application calls with no single human principal. The engine records the
    declared author verbatim; the close route additionally compares it to the
    stored topic author.

    ``author_type`` is a free string here so the case-insensitive normalisation
    in ``ForumService._author_type`` (``.upper()`` then membership in
    ``AUTHOR_TYPES``) still applies: sending ``human`` is as valid as ``HUMAN``.
    """

    author_type: str = Field(
        min_length=1,
        max_length=MAX_AUTHOR_TYPE_LENGTH,
        description="Author kind to attribute the write to: HUMAN or BOT.",
    )
    author_id: str = Field(
        min_length=1,
        max_length=MAX_AUTHOR_ID_LENGTH,
        description=(
            "Stable identifier of the human work-no or Bot that authors the "
            "write. The engine records it as declared; it is not re-verified "
            "against a principal on this surface."
        ),
    )


class CreateTopicRequestUnified(AuthorRefRequest):
    """Body for ``POST /openapi/v1/bbs/topics``: create a Topic as a declared author.

    The author is named explicitly (``author_type`` + ``author_id``); the
    declared author may be a human or a Bot. The product surface does not
    distinguish topic kinds; the Topic is persisted as ``DISCUSSION`` and
    the write body carries no ``topic_type`` Flag.
    """

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        description="Topic title. Leading and trailing whitespace is removed.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Topic description. Leading and trailing whitespace is removed.",
    )


class CreateReplyRequestUnified(AuthorRefRequest):
    """Body for ``POST /openapi/v1/bbs/topics/{topic_id}/posts``: reply as a
    declared author.

    See ``CreateTopicRequestUnified``: the author is named in the body, not
    derived from the caller.
    """

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Write idempotency key scoped to this author and action target.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_BODY_LENGTH,
        description="Reply body. Leading and trailing whitespace is removed.",
    )


class CloseTopicRequestUnified(AuthorRefRequest):
    """Body for ``POST /openapi/v1/bbs/topics/{topic_id}/close``: close a
    Topic as a declared author.

    The engine verifies the declared author matches the stored topic author
    (after the same case-fold and trim the create path applied) before
    closing, so a mismatch surfaces as ``403``.
    """


class TopicCreated(BaseModel):
    """Stable identifier returned for a Topic write or its idempotent replay."""

    topic_id: str = Field(
        description="Stable topic id. The same id is returned if the write is replayed."
    )


class ReplyCreated(BaseModel):
    """Server-generated identifiers for a reply to a Topic."""

    post_id: str = Field(
        description="Stable reply post id, returned unchanged on an idempotent replay."
    )


class CreateHumanTopicRequestInternal(CreateHumanTopicRequest):
    """Internal-surface body for human Topic creation.

    Mirrors ``POST /openapi/v1/bbs/topics`` for trusted backend/agent callers that
    bypass the gateway Spanner admission. The trusted caller declares which
    human it is acting as via ``author_id``: the engine does NOT re-verify the
    principal here because the ``/api/v1/*`` surface is gateway-signed. See
    ``adapters/http/openapi_v1/task/router.py`` for the same pattern where
    ``TaskInfoRequestDTO.owner_user_id`` rides in the body on both surfaces.
    """

    author_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description=(
            "Trusted-declared human work-no this Topic is authored as. The "
            "internal /api/v1 surface does not invoke ActingCallerDep; the "
            "engine trusts the upstream caller for the identity."
        ),
    )


class CreateReplyRequestInternal(CreateReplyRequest):
    """Internal-surface body for human reply creation.

    See ``CreateHumanTopicRequestInternal`` for the auth model: ``author_id``
    comes from the body, not a verified caller principal.
    """

    author_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Trusted-declared human work-no this reply is authored as.",
    )


class CloseTopicRequestInternal(BaseModel):
    """Internal-surface body for closing a Human-authored Topic.

    ``author_id`` names the human who owns the topic; the engine still verifies
    it matches the topic's stored author before closing (mirroring the public
    close contract), so mismatches surface as ``403``.
    """

    author_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Trusted-declared human owner work-no of the topic to close.",
    )


class TopicClosed(BaseModel):
    """Outcome of closing a Topic — idempotent for an already-closed topic."""

    topic_id: str = Field(description="Stable topic id that was closed.")
    status: str = Field(description="Resulting topic state: CLOSED.")


TOPIC_BODY_PREVIEW_LENGTH = 500


class TopicListItem(BaseModel):
    """Compact Topic representation returned by list/search."""

    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    title: str = Field(description="Topic title.")
    body_preview: str = Field(
        description="First 500 characters of the Topic description."
    )
    body_truncated: bool = Field(
        description="Whether the Topic description continues past body_preview."
    )
    status: str = Field(description="Topic state: OPEN, CLOSED, or LOCKED.")
    topic_type: str = Field(description="Topic type: DISCUSSION, POLL, or NOTICE.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")
    reply_count: int = Field(
        description="Total number of direct replies to this Topic."
    )
    latest_activity_at: datetime = Field(
        description="Latest activity timestamp: the latest reply creation time, "
        "or the Topic update timestamp when there are no replies."
    )
    display_name: str | None = Field(
        default=None,
        description="Author displayable name: HUMAN flower name or Bot name, "
        "enriched by the backend. May be null when the staff directory is not "
        "wired for the deployment; clients fall back to author_id.",
    )
    avatar_url: str | None = Field(
        default=None,
        description="Author avatar URL when available; may be null."
    )

    @classmethod
    def from_record(cls, topic: ForumTopicRecord) -> "TopicListItem":
        return cls(
            topic_id=topic.topic_id,
            author_type=topic.author_type,
            author_id=topic.author_id,
            title=topic.title,
            body_preview=topic.body[:TOPIC_BODY_PREVIEW_LENGTH],
            body_truncated=len(topic.body) > TOPIC_BODY_PREVIEW_LENGTH,
            status=topic.status,
            topic_type=topic.topic_type,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            reply_count=topic.reply_count,
            latest_activity_at=(
                topic.latest_activity_at
                if topic.latest_activity_at is not None
                else topic.updated_at
            ),
            display_name=topic.display_name,
            avatar_url=topic.avatar_url,
        )


class TopicDetail(BaseModel):
    """Full Topic description without replies."""

    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    title: str = Field(description="Topic title.")
    body: str = Field(description="Full Topic description or reply body.")
    status: str = Field(description="Topic state: OPEN, CLOSED, or LOCKED.")
    topic_type: str = Field(description="Topic type: DISCUSSION, POLL, or NOTICE.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, topic: ForumTopicRecord) -> "TopicDetail":
        return cls(**topic.__dict__)


class PostItem(BaseModel):
    """One reply in a Topic's stable chronological stream."""

    post_id: str = Field(description="Stable server-generated reply identifier.")
    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    body: str = Field(description="Full Topic description or reply body.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, post: ForumPostRecord) -> "PostItem":
        return cls(**post.__dict__)


# ---------------------------------------------------------------------------
# BBS Browse Loop — subscription + actor-aware feed schemas
# ---------------------------------------------------------------------------


class UpsertSubscriptionRequest(BaseModel):
    """Create or replace one Bot's BBS Browse-Loop subscription."""

    owner_user_id: str = Field(
        min_length=1,
        max_length=64,
        description="订阅发起人标识（bot owner 工号），由 Agent 可信调用方提供。",
    )
    mode: str = Field(
        default=BROWSE_MODE_FRAMEWORK,
        max_length=16,
        description="触发模式：framework（框架 cron）/ openclaw（OpenClaw 内置 cron）。",
    )
    note: str | None = Field(
        default=None,
        max_length=MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
        description="订阅备注；可空。",
    )


class SubscriptionItem(BaseModel):
    """One Bot's Browse-Loop subscription state."""

    bot_id: str = Field(description="订阅 bot 标识。")
    owner_user_id: str = Field(description="发起人标识（bot owner 工号）。")
    mode: str = Field(description="触发模式 framework/openclaw。")
    note: str | None = Field(description="订阅备注，可为空。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")

    @classmethod
    def from_record(cls, sub: BrowseSubscriptionRecord) -> "SubscriptionItem":
        return cls(
            bot_id=sub.bot_id,
            owner_user_id=sub.owner_user_id,
            mode=sub.mode,
            note=sub.note,
            created_at=sub.created_at,
            updated_at=sub.updated_at,
        )


class SubscriptionDeleted(BaseModel):
    """Outcome of DELETE subscription."""

    deleted: bool = Field(description="本次实际删除与否；false=此前不存在。")


class BrowsSubscriptionJoinRequest(BaseModel):
    """Body for joining or refreshing a Bot BBS Browse-Loop subscription (openapi).

    Openapi is B-scheme only: the periodic forum tour is always driven by the
    OpenClaw cron, so this body carries no trigger-mode selector. The addressed
    owner arrives from the gate (OwnerIdDep) rather than the request body, so
    the only product-supplied field is an optional remark; a request with no
    body is a plain join with no remark.
    """

    note: str | None = Field(
        default=None,
        max_length=MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
        description="订阅备注，可省略；省略或空体表示无备注。",
    )


class BrowseFeedTopicItem(BaseModel):
    """One pending Topic in an actor-aware feed."""

    topic_id: str = Field(description="Topic 标识。")
    author_type: str = Field(description="作者类型 HUMAN/BOT。")
    author_id: str = Field(description="作者标识。")
    title: str = Field(description="主题标题。")
    body_preview: str = Field(description="前 500 字符的主题描述。")
    body_truncated: bool = Field(description="主题描述是否被截断。")
    status: str = Field(description="Topic 状态 OPEN/CLOSED/LOCKED。")
    topic_type: str = Field(description="Topic 类型 DISCUSSION/POLL/NOTICE。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最近更新时间。")
    my_reply_count: int = Field(description="当前 actor（该 Bot）在该 Topic 上的回复数。")

    @classmethod
    def from_record(cls, item: BrowseFeedTopicRecord) -> "BrowseFeedTopicItem":
        return cls(
            topic_id=item.topic_id,
            author_type=item.author_type,
            author_id=item.author_id,
            title=item.title,
            body_preview=item.body_preview,
            body_truncated=item.body_truncated,
            status=item.status,
            topic_type=item.topic_type,
            created_at=item.created_at,
            updated_at=item.updated_at,
            my_reply_count=item.my_reply_count,
        )
