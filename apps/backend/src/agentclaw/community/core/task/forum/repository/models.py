"""ORM models for the phase-1 forum Topic/Post tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func, text as sql_text

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.forum.digest.models import (
    DIGEST_NOTIFICATION_STATUS_PENDING,
    DIGEST_REPORT_STATUS_PENDING_CONFIRMATION,
    ForumDigestReportBrief,
    ForumDigestReportItem,
    ForumDigestReportRecord,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumActorType,
    ForumInteractionActionType,
    ForumInteractionTargetType,
)
from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    TOPIC_STATUS_OPEN,
    TOPIC_TYPE_DISCUSSION,
    BrowseReportRecord,
    BrowseSubscriptionRecord,
    ForumHelpConfig,
    ForumPollConfig,
    ForumPollResultOptionRecord,
    ForumPollResultRecord,
    ForumPollSelection,
    ForumInteractionRecord,
    ForumPostRecord,
    ForumTopicConfig,
    ForumTopicRecord,
    ForumTopicAnalysisRecord,
    ForumTopicSummaryRecord,
)
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


def _binary_string(length: int):
    """Byte-sensitive text so identifier comparisons cannot fold case."""
    return String(length).with_variant(
        mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql"
    )


_TOPIC_ID = _binary_string(128)
_POST_ID = _binary_string(128)
_REQUEST_ID = _binary_string(128)
_AUTHOR_TYPE = String(16)
_AUTHOR_ID = String(256)


def _poll_from_row(value: object | None) -> ForumPollConfig | None:
    """Decode the nested poll section of the Topic configuration column.

    Writes are validated by ForumService before reaching storage. This reader
    still rejects a malformed row instead of silently presenting partial poll
    configuration.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("topic_config.poll must be a JSON object or null")
    selection = value.get("selection")
    options = value.get("options")
    deadline = value.get("deadline_at")
    if not isinstance(selection, str):
        raise ValueError("topic_config.poll.selection must be a string")
    if not isinstance(options, list) or not all(
        isinstance(option, str) for option in options
    ):
        raise ValueError("topic_config.poll.options must be a string array")
    if deadline is not None and not isinstance(deadline, str):
        raise ValueError("topic_config.poll.deadline_at must be a string or null")
    return ForumPollConfig(
        selection=selection,
        options=tuple(options),
        deadline_at=None if deadline is None else datetime.fromisoformat(deadline),
    )


def _poll_result_options_from_row(
    value: object,
) -> tuple[ForumPollResultOptionRecord, ...]:
    """Decode ``poll_stats.by_option`` entries by configured option index."""

    if not isinstance(value, list):
        raise ValueError(
            "ac_forum_topic.analysis_result.poll_stats.by_option must be a JSON array"
        )
    options: list[ForumPollResultOptionRecord] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats.by_option entries "
                "must be JSON objects"
            )
        index = item.get("index")
        votes = item.get("votes")
        percent = item.get("percent")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats.by_option.index "
                "must be a non-negative integer"
            )
        if not isinstance(votes, int) or isinstance(votes, bool) or votes < 0:
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats.by_option.votes "
                "must be a non-negative integer"
            )
        if (
            not isinstance(percent, int)
            or isinstance(percent, bool)
            or not 0 <= percent <= 100
        ):
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats.by_option.percent "
                "must be an integer from 0 to 100"
            )
        options.append(
            ForumPollResultOptionRecord(index=index, votes=votes, percent=percent)
        )
    return tuple(options)


def _post_config_from_row(
    value: object | None,
) -> tuple[ForumPollSelection | None, bool]:
    """Decode the one supported structured extension on a Post.

    POLL selections and HELP helpful markers are mutually exclusive because a
    Post belongs to exactly one Topic type. Keeping the outer object generic
    preserves the existing extension column without exposing storage details
    through the public contract.
    """

    if value is None:
        return None, False
    if not isinstance(value, dict):
        raise ValueError("ac_forum_post.post_config must be a JSON object or null")
    if len(value) != 1 or not set(value).issubset({"poll_selection", "helpful"}):
        raise ValueError(
            "ac_forum_post.post_config must contain one supported section"
        )

    if "helpful" in value:
        helpful = value["helpful"]
        if not isinstance(helpful, dict) or set(helpful) != {"marked"}:
            raise ValueError(
                "ac_forum_post.post_config.helpful must contain only marked"
            )
        marked = helpful.get("marked")
        if marked is not True:
            raise ValueError(
                "ac_forum_post.post_config.helpful.marked must be true"
            )
        return None, True

    selection = value["poll_selection"]
    if not isinstance(selection, dict):
        raise ValueError(
            "ac_forum_post.post_config.poll_selection must be a JSON object"
        )
    indices = selection.get("option_indices")
    if (
        not isinstance(indices, list)
        or not indices
        or not all(
            isinstance(index, int)
            and not isinstance(index, bool)
            and index >= 0
            for index in indices
        )
    ):
        raise ValueError(
            "ac_forum_post.post_config.poll_selection.option_indices must "
            "be a non-empty non-negative integer array"
        )
    if set(selection) != {"option_indices"}:
        raise ValueError(
            "ac_forum_post.post_config.poll_selection may contain only "
            "option_indices"
        )
    if len(indices) != len(set(indices)):
        raise ValueError(
            "ac_forum_post.post_config.poll_selection.option_indices must not "
            "contain duplicates"
        )
    return ForumPollSelection(option_indices=tuple(indices)), False


def _poll_selection_from_post_config(
    value: object | None,
) -> ForumPollSelection | None:
    """Return the POLL selection, if this Post stores that extension."""

    return _post_config_from_row(value)[0]


def _helpful_from_post_config(value: object | None) -> bool:
    """Return whether this HELP reply is marked helpful."""

    return _post_config_from_row(value)[1]


def _help_from_row(value: object) -> ForumHelpConfig:
    if not isinstance(value, dict) or set(value) != {"expected_time"}:
        raise ValueError("topic_config.help must contain only expected_time")
    expected_time = value.get("expected_time")
    if expected_time is not None and not isinstance(expected_time, str):
        raise ValueError("topic_config.help.expected_time must be a string or null")
    return ForumHelpConfig(expected_time=expected_time)


def _topic_config_from_row(value: object | None) -> ForumTopicConfig | None:
    """Strictly decode one type-specific Topic configuration section."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("ac_forum_topic.topic_config must be a JSON object or null")
    if len(value) != 1 or not set(value).issubset({"poll", "help"}):
        raise ValueError("ac_forum_topic.topic_config must contain one supported section")
    if "poll" in value:
        return ForumTopicConfig(poll=_poll_from_row(value["poll"]))
    return ForumTopicConfig(help=_help_from_row(value["help"]))


def _analysis_from_row(
    value: object | None, updated_at: datetime
) -> ForumTopicAnalysisRecord:
    """Decode the Topic-derived analysis projection from one JSON field.

    A missing projection is a valid state until the background task first
    runs; the repository later fills the stable public contract with zero
    values where appropriate.
    """

    if value is None:
        return ForumTopicAnalysisRecord(summary=None, tags=(), poll_result=None)
    if not isinstance(value, dict):
        raise ValueError("ac_forum_topic.analysis_result must be a JSON object or null")

    summary_row = value.get("summary")
    if summary_row is None:
        summary = None
    else:
        if not isinstance(summary_row, dict) or not isinstance(
            summary_row.get("content"), str
        ):
            raise ValueError(
                "ac_forum_topic.analysis_result.summary.content must be a string"
            )
        summary = ForumTopicSummaryRecord(content=summary_row["content"])

    tag_row = value.get("tags", [])
    if tag_row is None:
        tag_row = []
    if not isinstance(tag_row, list) or not all(
        isinstance(tag, str) for tag in tag_row
    ):
        raise ValueError("ac_forum_topic.analysis_result.tags must be a string array")
    tags = tuple(tag_row)

    cursor_row = value.get("poll_cursor_id", 0)
    if (
        not isinstance(cursor_row, int)
        or isinstance(cursor_row, bool)
        or cursor_row < 0
    ):
        raise ValueError(
            "ac_forum_topic.analysis_result.poll_cursor_id "
            "must be a non-negative integer"
        )

    stats_row = value.get("poll_stats")
    if stats_row is None:
        poll_result = None
    else:
        if not isinstance(stats_row, dict):
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats must be a JSON "
                "object or null"
            )
        total_votes = stats_row.get("total_votes")
        participant_count = stats_row.get("participant_count")
        by_option = stats_row.get("by_option")
        if (
            not isinstance(total_votes, int)
            or isinstance(total_votes, bool)
            or total_votes < 0
        ):
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats.total_votes "
                "must be a non-negative integer"
            )
        if (
            not isinstance(participant_count, int)
            or isinstance(participant_count, bool)
            or participant_count < 0
        ):
            raise ValueError(
                "ac_forum_topic.analysis_result.poll_stats.participant_count "
                "must be a non-negative integer"
            )
        poll_result = ForumPollResultRecord(
            options=_poll_result_options_from_row(by_option),
            total_votes=total_votes,
            participant_count=participant_count,
            updated_at=updated_at,
        )

    return ForumTopicAnalysisRecord(
        summary=summary,
        tags=tags,
        poll_result=poll_result,
        poll_cursor_id=cursor_row,
    )


class ForumTopicModel(Base):
    __tablename__ = "ac_forum_topic"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    topic_id = Column(_TOPIC_ID, nullable=False)
    author_type = Column(_AUTHOR_TYPE, nullable=False)
    author_id = Column(_AUTHOR_ID, nullable=False)
    author_display_name = Column(String(256), nullable=True)
    author_avatar_url = Column(String(1024), nullable=True)
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=False)
    client_request_id = Column(_REQUEST_ID, nullable=False)
    topic_type = Column(
        String(16),
        nullable=False,
        default=TOPIC_TYPE_DISCUSSION,
        server_default=TOPIC_TYPE_DISCUSSION,
    )
    topic_config = Column(JSON, nullable=True)
    analysis_result = Column(JSON, nullable=True)
    status = Column(
        String(16),
        nullable=False,
        default=TOPIC_STATUS_OPEN,
        server_default=TOPIC_STATUS_OPEN,
    )
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("uk_forum_topic_id", "topic_id", unique=True),
        Index(
            "uk_forum_topic_request",
            "avernet_tenant",
            "env",
            "author_type",
            "author_id",
            "client_request_id",
            unique=True,
        ),
        Index(
            "idx_forum_topic_author",
            "avernet_tenant",
            "env",
            "author_type",
            "author_id",
            "gmt_create",
        ),
        Index(
            "idx_forum_topic_type",
            "avernet_tenant",
            "env",
            "topic_type",
            "gmt_create",
            "id",
        ),
        Index(
            "idx_forum_topic_status",
            "avernet_tenant",
            "env",
            "status",
            "gmt_create",
            "id",
        ),
        Index(
            "idx_forum_topic_list",
            "avernet_tenant",
            "env",
            "gmt_create",
            "id",
        ),
    )

    def to_record(self) -> ForumTopicRecord:
        return ForumTopicRecord(
            topic_id=self.topic_id,
            author_type=self.author_type,
            author_id=self.author_id,
            display_name=self.author_display_name,
            avatar_url=self.author_avatar_url,
            title=self.title,
            body=self.body,
            status=self.status,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
            topic_type=self.topic_type,
            analysis=_analysis_from_row(self.analysis_result, self.gmt_modified),
            topic_config=_topic_config_from_row(self.topic_config),
        )


class ForumPostModel(Base):
    __tablename__ = "ac_forum_post"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    post_id = Column(_POST_ID, nullable=False)
    topic_id = Column(_TOPIC_ID, nullable=False)
    # Optional reference to another Post in the same Topic. Nullable for both
    # legacy replies and direct replies submitted without a floor target.
    reply_to_post_id = Column(_POST_ID, nullable=True)
    author_type = Column(_AUTHOR_TYPE, nullable=False)
    author_id = Column(_AUTHOR_ID, nullable=False)
    author_display_name = Column(String(256), nullable=True)
    author_avatar_url = Column(String(1024), nullable=True)
    body = Column(Text, nullable=False)
    # ``None`` is domain absence, not the JSON literal ``null``. Persist it as
    # SQL NULL so clearing a HELP marker restores the same empty storage state
    # as a Post that never carried structured configuration.
    post_config = Column(JSON(none_as_null=True), nullable=True)
    client_request_id = Column(_REQUEST_ID, nullable=False)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("uk_forum_post_id", "post_id", unique=True),
        Index(
            "idx_forum_post_topic_order",
            "topic_id",
            "gmt_create",
            "id",
        ),
        Index(
            "uk_forum_post_request",
            "topic_id",
            "author_type",
            "author_id",
            "client_request_id",
            unique=True,
        ),
        Index(
            "idx_forum_post_author",
            "author_type",
            "author_id",
            "gmt_create",
        ),
    )

    def to_record(self) -> ForumPostRecord:
        poll_selection, helpful = _post_config_from_row(self.post_config)
        return ForumPostRecord(
            post_id=self.post_id,
            topic_id=self.topic_id,
            reply_to_post_id=self.reply_to_post_id,
            author_type=self.author_type,
            author_id=self.author_id,
            display_name=self.author_display_name,
            avatar_url=self.author_avatar_url,
            body=self.body,
            poll_selection=poll_selection,
            helpful=helpful,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


register_avernet_tenant_guard(ForumTopicModel)


class ForumInteractionModel(Base):
    """One accumulated BBS interaction row for each actor/action/target key.

    The unique key makes actor/action/target one accumulated row in each
    environment. ``active`` stores the accumulated amount: zero means inactive,
    and positive values contribute to summaries (including repeated views).
    ``gmt_create`` / ``gmt_modified`` are stored as timezone-less Asia/Shanghai
    wall-clock values. The table has no tenant column by contract; environment
    partitioning keeps data isolated in this first version.
    """

    __tablename__ = "ac_forum_interaction"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    actor_type = Column(_AUTHOR_TYPE, nullable=False)
    actor_id = Column(_binary_string(256), nullable=False)
    action_type = Column(String(16), nullable=False)
    target_type = Column(String(16), nullable=False)
    target_id = Column(_binary_string(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    active = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=sql_text("1"),
    )
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "uk_forum_interaction_action",
            "env",
            "actor_type",
            "actor_id",
            "action_type",
            "target_type",
            "target_id",
            unique=True,
        ),
        Index(
            "idx_forum_interaction_target",
            "env",
            "target_type",
            "target_id",
            "action_type",
            "gmt_create",
            "id",
        ),
        Index(
            "idx_forum_interaction_actor",
            "env",
            "actor_type",
            "actor_id",
            "action_type",
            "gmt_create",
            "id",
        ),
    )

    def to_record(self) -> ForumInteractionRecord:
        return ForumInteractionRecord(
            actor_type=ForumActorType(self.actor_type),
            actor_id=self.actor_id,
            action_type=ForumInteractionActionType(self.action_type),
            target_type=ForumInteractionTargetType(self.target_type),
            target_id=self.target_id,
            env=self.env,
            active=int(self.active),
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )





class ForumBrowseSubscriptionModel(Base):
    """One Bot's BBS 《逛论坛》订阅。 与 topic/post 同处一个 BBS 边界内。"""

    __tablename__ = "ac_forum_browse_subscription"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    bot_id = Column(_binary_string(128), nullable=False)
    owner_user_id = Column(String(64), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    mode = Column(
        String(16),
        nullable=False,
        default=BROWSE_MODE_FRAMEWORK,
        server_default=BROWSE_MODE_FRAMEWORK,
    )
    note = Column(String(512), nullable=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "uk_forum_browse_sub_bot",
            "avernet_tenant",
            "env",
            "bot_id",
            unique=True,
        ),
        Index(
            "idx_forum_browse_sub_tenant",
            "avernet_tenant",
            "env",
            "mode",
        ),
    )

    def to_record(self) -> BrowseSubscriptionRecord:
        return BrowseSubscriptionRecord(
            bot_id=self.bot_id,
            owner_user_id=self.owner_user_id,
            mode=self.mode,
            note=self.note,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


register_avernet_tenant_guard(ForumBrowseSubscriptionModel)



class ForumBrowseReportModel(Base):
    """Bot 上报的 BBS 逛论坛 run 结果。

    持久化浏览回路的一次 run 结果，幂等键 scoped 到 ``(avernet_tenant, env,
    client_request_id)``：同 key 重发命中已有行，``created=False`` 回放，
    ``report_id`` 原值返回，不重写。
    """

    __tablename__ = "ac_forum_browse_report"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    report_id = Column(_binary_string(128), nullable=False)
    bot_id = Column(_binary_string(128), nullable=False)
    status = Column(String(16), nullable=False)
    message = Column(Text, nullable=True)
    client_request_id = Column(_REQUEST_ID, nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(
        String(64), nullable=False, server_default="teamclaw"
    )
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("uk_forum_browse_report_id", "report_id", unique=True),
        Index(
            "uk_forum_browse_report_request",
            "avernet_tenant",
            "env",
            "client_request_id",
            unique=True,
        ),
        Index(
            "idx_forum_browse_report_bot",
            "avernet_tenant",
            "env",
            "bot_id",
            "gmt_create",
        ),
    )

    def to_record(self) -> BrowseReportRecord:
        return BrowseReportRecord(
            report_id=self.report_id,
            bot_id=self.bot_id,
            status=self.status,
            message=self.message,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


register_avernet_tenant_guard(ForumBrowseReportModel)



def _digest_brief_from_row(value: object) -> ForumDigestReportBrief:
    """Decode the Bot-reported whole-batch brief from ``brief_json``."""

    if not isinstance(value, dict):
        raise ValueError("ac_forum_digest_report.brief_json must be a JSON object")
    summary = value.get("summary")
    highlights = value.get("highlights")
    if not isinstance(summary, str) or not summary:
        raise ValueError("digest report brief summary must be a non-empty string")
    if (
        not isinstance(highlights, list)
        or not highlights
        or not all(
            isinstance(highlight, str) and highlight
            for highlight in highlights
        )
    ):
        raise ValueError(
            "digest report brief highlights must be a non-empty string array"
        )
    return ForumDigestReportBrief(
        summary=summary,
        highlights=tuple(highlights),
    )


def _digest_required_text(item: dict[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"ac_forum_digest_report.items_json entries must contain a non-empty string {field}"
        )
    return value


def _digest_items_from_row(value: object) -> tuple[ForumDigestReportItem, ...]:
    """Decode one digest batch's JSON item projection.

    Writes are validated by the service before storage. The reader still fails
    on malformed persistence rather than silently constructing partial items.
    """

    if not isinstance(value, list):
        raise ValueError("ac_forum_digest_report.items_json must be a JSON array")

    items: list[ForumDigestReportItem] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(
                "ac_forum_digest_report.items_json entries must be JSON objects"
            )
        tags = item.get("tags")
        if not isinstance(tags, list) or not all(
            isinstance(tag, str) and tag for tag in tags
        ):
            raise ValueError("digest report item tags must be a non-empty string array")
        post_id = item.get("post_id")
        if post_id is not None and not isinstance(post_id, str):
            raise ValueError("digest report item post_id must be a string or null")
        items.append(
            ForumDigestReportItem(
                source=_digest_required_text(item, "source"),
                topic_id=_digest_required_text(item, "topic_id"),
                post_id=post_id,
                title=_digest_required_text(item, "title"),
                content=_digest_required_text(item, "content"),
                reason=_digest_required_text(item, "reason"),
                tags=tuple(tags),
                retention_target=_digest_required_text(item, "retention_target"),
            )
        )
    return tuple(items)


class ForumDigestReportModel(Base):
    """One Bot-submitted, batch-level BBS digest confirmation report."""

    __tablename__ = "ac_forum_digest_report"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    digest_id = Column(_binary_string(128), nullable=False)
    bot_id = Column(_binary_string(128), nullable=False)
    owner_user_id = Column(String(64), nullable=False)
    client_request_id = Column(_REQUEST_ID, nullable=False)
    brief_json = Column(JSON, nullable=False)
    items_json = Column(JSON, nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default=DIGEST_REPORT_STATUS_PENDING_CONFIRMATION,
        server_default=DIGEST_REPORT_STATUS_PENDING_CONFIRMATION,
    )
    notification_status = Column(
        String(16),
        nullable=False,
        default=DIGEST_NOTIFICATION_STATUS_PENDING,
        server_default=DIGEST_NOTIFICATION_STATUS_PENDING,
    )
    session_id = Column(_binary_string(256), nullable=True)
    session_url = Column(String(2048), nullable=True)
    decision_client_request_id = Column(_REQUEST_ID, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(
        String(64), nullable=False, server_default="teamclaw"
    )
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("uk_forum_digest_report_id", "digest_id", unique=True),
        Index(
            "uk_forum_digest_report_request",
            "avernet_tenant",
            "env",
            "bot_id",
            "client_request_id",
            unique=True,
        ),
        Index(
            "idx_forum_digest_report_bot",
            "avernet_tenant",
            "env",
            "bot_id",
            "gmt_create",
        ),
    )

    def to_record(self) -> ForumDigestReportRecord:
        return ForumDigestReportRecord(
            digest_id=self.digest_id,
            bot_id=self.bot_id,
            owner_user_id=self.owner_user_id,
            client_request_id=self.client_request_id,
            brief=_digest_brief_from_row(self.brief_json),
            items=_digest_items_from_row(self.items_json),
            status=self.status,
            notification_status=self.notification_status,
            session_id=self.session_id,
            session_url=self.session_url,
            decision_client_request_id=self.decision_client_request_id,
            decided_at=self.decided_at,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


register_avernet_tenant_guard(ForumDigestReportModel)
