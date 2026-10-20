"""ORM models for the phase-1 forum Topic/Post tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    TOPIC_STATUS_OPEN,
    TOPIC_TYPE_DISCUSSION,
    BrowseReportRecord,
    BrowseSubscriptionRecord,
    ForumPollConfig,
    ForumPollResultOptionRecord,
    ForumPollResultRecord,
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


def _topic_config_from_row(value: object | None) -> ForumTopicConfig | None:
    """Decode the generic Topic configuration JSON.

    ``topic_type`` selects which configuration section is meaningful. The
    current contract only defines ``poll``; future Topic types can add other
    keys to this object without another migration.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("ac_forum_topic.topic_config must be a JSON object or null")
    return ForumTopicConfig(poll=_poll_from_row(value.get("poll")))


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

    return ForumTopicAnalysisRecord(summary=summary, tags=tags, poll_result=poll_result)


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
        return ForumPostRecord(
            post_id=self.post_id,
            topic_id=self.topic_id,
            reply_to_post_id=self.reply_to_post_id,
            author_type=self.author_type,
            author_id=self.author_id,
            display_name=self.author_display_name,
            avatar_url=self.author_avatar_url,
            body=self.body,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


register_avernet_tenant_guard(ForumTopicModel)


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
