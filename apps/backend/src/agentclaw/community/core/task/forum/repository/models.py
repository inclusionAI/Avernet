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
    TOPIC_TAG_SOURCE_LLM,
    TOPIC_TYPE_DISCUSSION,
    BrowseReportRecord,
    BrowseSubscriptionRecord,
    ForumPollConfig,
    ForumPollResultOptionRecord,
    ForumPollResultRecord,
    ForumPostRecord,
    ForumTopicRecord,
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
    """Decode the canonical Topic-level JSON poll column.

    Writes are validated by ForumService before reaching storage. This reader
    still rejects a malformed row instead of silently presenting partial poll
    configuration.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("ac_forum_topic.poll_config must be a JSON object or null")
    selection = value.get("selection")
    options = value.get("options")
    deadline = value.get("deadline_at")
    if not isinstance(selection, str):
        raise ValueError("ac_forum_topic.poll_config.selection must be a string")
    if not isinstance(options, list) or not all(
        isinstance(option, str) for option in options
    ):
        raise ValueError("ac_forum_topic.poll_config.options must be a string array")
    if deadline is not None and not isinstance(deadline, str):
        raise ValueError("ac_forum_topic.poll_config.deadline_at must be a string or null")
    return ForumPollConfig(
        selection=selection,
        options=tuple(options),
        deadline_at=None if deadline is None else datetime.fromisoformat(deadline),
    )


def _poll_result_options_from_row(value: object) -> tuple[ForumPollResultOptionRecord, ...]:
    """Decode the poll aggregate projection, rejecting malformed rows."""

    if not isinstance(value, list):
        raise ValueError("ac_forum_topic_poll_result.options_result must be a JSON array")
    options: list[ForumPollResultOptionRecord] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(
                "ac_forum_topic_poll_result.options_result entries must be JSON objects"
            )
        label = item.get("label")
        votes = item.get("votes")
        percent = item.get("percent")
        if not isinstance(label, str):
            raise ValueError(
                "ac_forum_topic_poll_result.options_result.label must be a string"
            )
        if not isinstance(votes, int) or isinstance(votes, bool) or votes < 0:
            raise ValueError(
                "ac_forum_topic_poll_result.options_result.votes must be a non-negative integer"
            )
        if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100:
            raise ValueError(
                "ac_forum_topic_poll_result.options_result.percent must be an integer from 0 to 100"
            )
        options.append(
            ForumPollResultOptionRecord(label=label, votes=votes, percent=percent)
        )
    return tuple(options)


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
    poll_config = Column(JSON, nullable=True)
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
            poll=_poll_from_row(self.poll_config),
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


class ForumPollResultModel(Base):
    """Persisted derived aggregate for one POLL Topic.

    The row is written by a backend aggregation task, not by the public vote
    write path. ``options_result`` keeps each configured option's label, vote
    count, and 0--100 integer percentage so the read API remains stable while
    later tasks can add incremental bookkeeping.
    """

    __tablename__ = "ac_forum_topic_poll_result"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    topic_id = Column(_TOPIC_ID, nullable=False)
    total_votes = Column(Integer, nullable=False, default=0, server_default="0")
    participant_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    options_result = Column(JSON, nullable=False)
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
        Index(
            "uk_forum_topic_poll_result",
            "avernet_tenant",
            "env",
            "topic_id",
            unique=True,
        ),
    )

    def to_record(self) -> ForumPollResultRecord:
        return ForumPollResultRecord(
            options=_poll_result_options_from_row(self.options_result),
            total_votes=self.total_votes,
            participant_count=self.participant_count,
            updated_at=self.gmt_modified,
        )


class ForumTopicTagModel(Base):
    """One derived or manually assigned label attached to a Topic."""

    __tablename__ = "ac_forum_topic_tag"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    topic_id = Column(_TOPIC_ID, nullable=False)
    tag_text = Column(String(64), nullable=False)
    source = Column(String(16), nullable=False, server_default=TOPIC_TAG_SOURCE_LLM)
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
        Index(
            "uk_forum_topic_tag",
            "avernet_tenant",
            "env",
            "topic_id",
            "tag_text",
            unique=True,
        ),
        Index(
            "idx_forum_topic_tag_text",
            "avernet_tenant",
            "env",
            "tag_text",
        ),
    )


register_avernet_tenant_guard(ForumPollResultModel)
register_avernet_tenant_guard(ForumTopicTagModel)


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
