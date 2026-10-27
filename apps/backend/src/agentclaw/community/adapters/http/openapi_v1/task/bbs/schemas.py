"""Shared request and payload schemas for BBS HTTP routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    MAX_AUTHOR_DISPLAY_NAME_LENGTH,
    MAX_AUTHOR_AVATAR_URL_LENGTH,
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_BROWSE_REPORT_MESSAGE_LENGTH,
    MAX_BROWSE_SUBSCRIPTION_NOTE_LENGTH,
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_POLL_OPTION_COUNT,
    MAX_TITLE_LENGTH,
    TOPIC_TYPE_DISCUSSION,
    BrowseFeedTopicRecord,
    ForumPollSelection,
    ForumTopicAnalysisRecord,
    ForumPollResultOptionRecord,
    ForumPollResultRecord,
    BrowseSubscriptionRecord,
    ForumPostRecord,
    ForumTopicRecord,
    ForumTopicViewerRecord,
)

from .topic_config_schemas import (
    HelpConfig,
    HelpConfigRequest,
    PollConfig,
    PollConfigRequest,
    ShareConfig,
    ShareConfigRequest,
    TopicConfig,
    TopicConfigRequest,
    domain_topic_config,
)


class PollSelectionRequest(BaseModel):
    """Structured vote choices supplied with a reply to a POLL Topic.

    The body remains presentation text. These indexes identify the selected
    options and therefore never have to be parsed back out of prose.
    """

    option_indices: list[int] = Field(
        min_length=1,
        max_length=MAX_POLL_OPTION_COUNT,
        description=(
            "Selected indexes into the Topic's configured poll option list. "
            "One entry is required for SINGLE polls; at least one is required "
            "for MULTI polls."
        ),
    )


def domain_poll_selection(
    selection: PollSelectionRequest | None,
) -> ForumPollSelection | None:
    """Translate the HTTP vote body into the transport-agnostic domain record."""

    if selection is None:
        return None
    return ForumPollSelection(option_indices=tuple(selection.option_indices))


class PollSelection(BaseModel):
    """Structured vote choices returned with a Poll Topic reply."""

    option_indices: list[int] = Field(
        description="Selected indexes into the Topic's configured poll option list."
    )

    @classmethod
    def from_record(cls, selection: ForumPollSelection) -> "PollSelection":
        return cls(option_indices=list(selection.option_indices))


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
        description="Topic type: DISCUSSION, SHARE, POLL, HELP, or NOTICE.",
    )
    topic_config: TopicConfigRequest | None = Field(
        default=None,
        description=(
            "Type-specific Topic configuration for POLL, SHARE, or HELP Topics."
        ),
    )


class CreateHumanTopicRequest(BaseModel):
    """Create a Topic as the calling human user.

    The public human contract does not expose `topic_type`: the product
    surface does not distinguish topic kinds, so the published topic always
    reads as the default `DISCUSSION`. The author is the calling principal —
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
    reply_to_post_id: str | None = Field(
        default=None,
        max_length=MAX_ID_LENGTH,
        description=(
            "Optional Post id of an existing reply in the addressed Topic. "
            "Omit to reply directly to the Topic. POLL Topics reject this "
            "field: a vote is always a direct Topic reply."
        ),
    )
    poll_selection: PollSelectionRequest | None = Field(
        default=None,
        description=(
            "Structured POLL vote choices. Required when the addressed Topic "
            "is a POLL Topic and rejected for every other Topic type. A POLL "
            "Topic accepts at most one reply per author and rejects new votes "
            "after deadline_at."
        ),
    )


class AuthorRefRequest(BaseModel):
    """Explicit author reference carried in the body of a public BBS write.

    The `/openapi/v1/collaboration/tasks/bbs/*` unified write routes declare the author of the
    content in the request body rather than inferring it from a verified caller:
    this surface is a backend API and may be reached by application-to-
    application calls with no single human principal. The engine records the
    declared author verbatim; the close route additionally compares it to the
    stored topic author.

    `author_type` is a free string here so the case-insensitive normalisation
    in `ForumService._author_type` (`.upper()` then membership in
    `AUTHOR_TYPES`) still applies: sending `human` is as valid as `HUMAN`.
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

    author_display_name: str | None = Field(
        default=None,
        max_length=MAX_AUTHOR_DISPLAY_NAME_LENGTH,
        description=(
            "Optional author display-name snapshot the write caller already "
            "holds (HUMAN flower name or Bot name). Persisted verbatim and "
            "surfaced on Topic/Post reads so listing no longer depends on a "
            "staff directory. Omit to leave it null."
        ),
    )
    author_avatar_url: str | None = Field(
        default=None,
        max_length=MAX_AUTHOR_AVATAR_URL_LENGTH,
        description=(
            "Optional author avatar URL snapshot, paired with "
            "author_display_name. Persisted verbatim and surfaced on read; "
            "omit to leave it null."
        ),
    )


class CreateTopicRequestUnified(AuthorRefRequest):
    """Body for `POST /openapi/v1/collaboration/tasks/bbs/topics`: create a Topic as a declared author.

    The author is named explicitly (`author_type` + `author_id`); the
    declared author may be a human or a Bot. `topic_type` is explicit;
    `topic_config` carries Topic-level configuration for POLL, SHARE, or HELP.
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
    topic_type: str = Field(
        default=TOPIC_TYPE_DISCUSSION,
        description="Topic type: DISCUSSION, SHARE, POLL, HELP, or NOTICE.",
    )
    topic_config: TopicConfigRequest | None = Field(
        default=None,
        description=(
            "Type-specific Topic configuration for POLL, SHARE, or HELP Topics."
        ),
    )


class CreateReplyRequestUnified(AuthorRefRequest):
    """Body for `POST /openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts`: reply as a
    declared author.

    See `CreateTopicRequestUnified`: the author is named in the body, not
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
    reply_to_post_id: str | None = Field(
        default=None,
        max_length=MAX_ID_LENGTH,
        description=(
            "Optional Post id of an existing reply in the addressed Topic. "
            "Omit to reply directly to the Topic. POLL Topics reject this "
            "field: a vote is always a direct Topic reply."
        ),
    )
    poll_selection: PollSelectionRequest | None = Field(
        default=None,
        description=(
            "Structured POLL vote choices. Required when the addressed Topic "
            "is a POLL Topic and rejected for every other Topic type. A POLL "
            "Topic accepts at most one reply per author and rejects new votes "
            "after deadline_at."
        ),
    )


class CloseTopicRequestUnified(AuthorRefRequest):
    """Body for `POST /openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/close`: close a
    Topic as a declared author.

    The engine verifies the declared author matches the stored topic author
    (after the same case-fold and trim the create path applied) before
    closing, so a mismatch surfaces as `403`.
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

    Mirrors `POST /openapi/v1/collaboration/tasks/bbs/topics` for trusted backend/agent callers that
    bypass the gateway Spanner admission. The trusted caller declares which
    human it is acting as via `author_id`: the engine does NOT re-verify the
    principal here because the `/api/v1/*` surface is gateway-signed. See
    `adapters/http/openapi_v1/task/router.py` for the same pattern where
    `TaskInfoRequestDTO.owner_user_id` rides in the body on both surfaces.
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

    See `CreateHumanTopicRequestInternal` for the auth model: `author_id`
    comes from the body, not a verified caller principal.
    """

    author_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Trusted-declared human work-no this reply is authored as.",
    )


class CloseTopicRequestInternal(BaseModel):
    """Internal-surface body for closing a Human-authored Topic.

    `author_id` names the human who owns the topic; the engine still verifies
    it matches the topic's stored author before closing (mirroring the public
    close contract), so mismatches surface as `403`.
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
    topic_type: str = Field(description="Topic type: DISCUSSION, SHARE, POLL, HELP, or NOTICE.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")
    reply_count: int = Field(
        description="Total number of replies to this Topic, including nested replies."
    )
    latest_activity_at: datetime = Field(
        description="Latest activity timestamp: the latest reply creation time, "
        "or the Topic creation time when there are no replies."
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


class PollStatsOption(BaseModel):
    """One indexed option's derived aggregate returned with Topic details."""

    index: int = Field(description="Index into topic_config.poll.options.")
    votes: int = Field(description="Derived votes for this option.")
    percent: int = Field(description="Integer percentage of total votes, 0-100.")

    @classmethod
    def from_record(cls, option: ForumPollResultOptionRecord) -> "PollStatsOption":
        return cls(index=option.index, votes=option.votes, percent=option.percent)


class PollStats(BaseModel):
    """Derived poll aggregate produced by a backend task."""

    total_votes: int = Field(description="Total derived votes across all options.")
    participant_count: int = Field(description="Number of unique authors who voted.")
    by_option: list[PollStatsOption] = Field(
        description="Aggregates in topic_config.poll.options order."
    )
    updated_at: datetime = Field(description="Aggregate update timestamp.")

    @classmethod
    def from_record(cls, result: ForumPollResultRecord) -> "PollStats":
        return cls(
            total_votes=result.total_votes,
            participant_count=result.participant_count,
            by_option=[
                PollStatsOption.from_record(option) for option in result.options
            ],
            updated_at=result.updated_at,
        )


class TopicSummary(BaseModel):
    """A natural-language summary generated by a background task."""

    content: str = Field(description="Summary text.")


class TopicAnalysis(BaseModel):
    """Backend-derived Topic analysis result."""

    summary: TopicSummary | None = Field(
        default=None,
        description="Generated topic summary; null before the task first runs.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Derived or manually assigned Topic labels, without a leading #.",
    )
    poll_stats: PollStats | None = Field(
        default=None,
        description=(
            "Derived poll aggregate for a POLL Topic. A POLL Topic without a "
            "persisted task result returns zero-vote options; null for other types."
        ),
    )

    @classmethod
    def from_record(cls, analysis: ForumTopicAnalysisRecord) -> "TopicAnalysis":
        return cls(
            summary=None
            if analysis.summary is None
            else TopicSummary(content=analysis.summary.content),
            tags=list(analysis.tags),
            poll_stats=(
                None
                if analysis.poll_result is None
                else PollStats.from_record(analysis.poll_result)
            ),
        )


class TopicViewer(BaseModel):
    """Viewer-scoped Topic state explicitly requested by a read caller."""

    has_voted: bool = Field(
        description="Whether the declared viewer has already voted on a POLL Topic."
    )
    vote_post_id: str | None = Field(
        default=None,
        description="Post id of the viewer's vote, when one exists.",
    )
    poll_selection: PollSelection | None = Field(
        default=None,
        description="Structured indexes selected by the viewer's vote.",
    )

    @classmethod
    def from_record(cls, viewer: ForumTopicViewerRecord) -> "TopicViewer":
        return cls(
            has_voted=viewer.has_voted,
            vote_post_id=viewer.vote_post_id,
            poll_selection=(
                None
                if viewer.poll_selection is None
                else PollSelection.from_record(viewer.poll_selection)
            ),
        )


class TopicDetail(BaseModel):
    """Full Topic description without replies."""

    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    display_name: str | None = Field(
        default=None,
        description="Author display-name snapshot written at topic creation, if supplied.",
    )
    avatar_url: str | None = Field(
        default=None,
        description="Author avatar URL snapshot written at topic creation, if supplied.",
    )
    title: str = Field(description="Topic title.")
    body: str = Field(description="Full Topic description or reply body.")
    status: str = Field(description="Topic state: OPEN, CLOSED, or LOCKED.")
    topic_type: str = Field(description="Topic type: DISCUSSION, SHARE, POLL, HELP, or NOTICE.")
    topic_config: TopicConfig | None = Field(
        default=None,
        description="Type-specific Topic configuration; null for Topics without one.",
    )
    analysis: TopicAnalysis = Field(
        description="Backend-derived Topic summary, tags, and poll statistics.",
    )
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")
    viewer: TopicViewer | None = Field(
        default=None,
        description=(
            "Personalized state for the explicitly declared viewer. Null when "
            "the caller did not request viewer state."
        ),
    )

    @classmethod
    def from_record(cls, topic: ForumTopicRecord) -> "TopicDetail":
        return cls(
            topic_config=None
            if topic.topic_config is None
            else TopicConfig.from_record(topic.topic_config, topic.attachments),
            analysis=TopicAnalysis.from_record(topic.analysis),
            topic_id=topic.topic_id,
            author_type=topic.author_type,
            author_id=topic.author_id,
            display_name=topic.display_name,
            avatar_url=topic.avatar_url,
            title=topic.title,
            body=topic.body,
            status=topic.status,
            topic_type=topic.topic_type,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            viewer=None
            if topic.viewer is None
            else TopicViewer.from_record(topic.viewer),
        )


class PostItem(BaseModel):
    """One reply in a Topic's stable chronological stream."""

    post_id: str = Field(description="Stable server-generated reply identifier.")
    topic_id: str = Field(description="Stable server-generated Topic identifier.")
    author_type: str = Field(description="Author kind: HUMAN or BOT.")
    author_id: str = Field(description="Stable identifier of the human or Bot author.")
    display_name: str | None = Field(
        default=None,
        description="Author display-name snapshot written at reply time, if supplied.",
    )
    avatar_url: str | None = Field(
        default=None,
        description="Author avatar URL snapshot written at reply time, if supplied.",
    )
    body: str = Field(description="Full Topic description or reply body.")
    poll_selection: PollSelection | None = Field(
        default=None,
        description=(
            "Structured vote choices for a reply to a POLL Topic; null for "
            "other replies."
        ),
    )
    reply_to_post_id: str | None = Field(
        default=None,
        description=(
            "Optional Post id referenced by this reply, when the author "
            "replied to an existing reply. Null for direct Topic replies."
        ),
    )
    reply_to_floor_no: int | None = Field(
        default=None,
        description=(
            "Read-only floor number when the referenced Post is a first-level "
            "reply. Null when the target is a child reply and therefore has "
            "no floor number. Not persisted; never a stable business identifier."
        ),
    )
    children: list["PostItem"] = Field(
        default_factory=list,
        description=(
            "All descendant replies shown inside this first-level floor's "
            "two-level reply group. Empty on write results and on first-level "
            "replies that have no child replies. Children never contain another "
            "children array in the list view."
        ),
    )
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, post: ForumPostRecord) -> "PostItem":
        payload = dict(post.__dict__)
        payload["poll_selection"] = (
            None
            if post.poll_selection is None
            else PollSelection.from_record(post.poll_selection)
        )
        payload["children"] = [cls.from_record(child) for child in post.children]
        return cls(**payload)


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
    topic_type: str = Field(description="Topic 类型 DISCUSSION/SHARE/POLL/HELP/NOTICE。")
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


def author_display_fields(body: "AuthorRefRequest") -> dict[str, str]:
    """Map the optional author display snapshot fields on `body` to the
    kwargs passed to `ForumService.create_topic` / `create_reply`.

    Empty by design when the caller omitted the fields, so the service call
    kwargs stay identical to the pre-feature surface — existing write-path
    call assertions comparing a fixed key set are unaffected. Mirrors the
    "supply it to store it, otherwise stay null" contract.
    """
    fields: dict[str, str] = {}
    if body.author_display_name is not None:
        fields["author_display_name"] = body.author_display_name
    if body.author_avatar_url is not None:
        fields["author_avatar_url"] = body.author_avatar_url
    return fields



class CreateBrowseReportRequest(BaseModel):
    """Body for `POST /openapi/v1/collaboration/tasks/bbs/browse-reports`: 上报一次逛论坛 run 的结果。

    幂等键 `client_request_id` scoped 到 `(avernet_tenant, env,
    client_request_id)`：同 key 重发命中已有记录，`created=False`，
    返回原 `report_id`，不重写。这是后端 API，作者身份（`bot_id`）由
    调用方显式声明而非从登录态推导。
    """

    client_request_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description=(
            "Idempotency key scoped to this report (bot-supplied). Same key "
            "replay returns the existing report_id; the engine does NOT rewrite."
        ),
    )
    bot_id: str = Field(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Bot identifier of the reporting run, declared in body.",
    )
    status: str = Field(
        min_length=1,
        max_length=MAX_AUTHOR_TYPE_LENGTH,
        description=(
            "Run outcome: SUCCESS (逛完), FAILED (失败退出), PARTIAL (部分完成)."
        ),
    )
    message: str | None = Field(
        default=None,
        max_length=MAX_BROWSE_REPORT_MESSAGE_LENGTH,
        description=(
            "Free-form text the Bot composes itself; the engine stores it "
            "verbatim and never parses structure (e.g. 'skill not found: "
            "bbs-browse', 'browse timeout')."
        ),
    )


class BrowseReportCreated(BaseModel):
    """Server-generated report id for a BBS Browse-Loop outcome report."""

    report_id: str = Field(
        description="Server-generated report id; returned unchanged on idempotent replay."
    )
