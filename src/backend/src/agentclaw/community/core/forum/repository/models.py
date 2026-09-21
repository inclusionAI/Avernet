"""ORM models for the phase-1 forum Topic/Post tables."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    TOPIC_STATUS_OPEN,
    TOPIC_TYPE_DISCUSSION,
    BrowseSubscriptionRecord,
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
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=False)
    client_request_id = Column(_REQUEST_ID, nullable=False)
    topic_type = Column(
        String(16),
        nullable=False,
        default=TOPIC_TYPE_DISCUSSION,
        server_default=TOPIC_TYPE_DISCUSSION,
    )
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
            title=self.title,
            body=self.body,
            status=self.status,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
            topic_type=self.topic_type,
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
    author_type = Column(_AUTHOR_TYPE, nullable=False)
    author_id = Column(_AUTHOR_ID, nullable=False)
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
            author_type=self.author_type,
            author_id=self.author_id,
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
