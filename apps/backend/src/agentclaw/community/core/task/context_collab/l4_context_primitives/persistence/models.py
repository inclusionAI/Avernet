"""ORM storage for Collet, independent Task authority and append-only Events.

Models mirror the currently deployed schema exactly. New writes serialize L4
scope/audit/resources/creation facts and the root-Task pointer into the real
``context_spec`` column. Tests build these models on SQLite.
``current_snapshot`` is retained only for explicit legacy decoding; new writes
persist Task and derive response views.
"""

from __future__ import annotations

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func
from sqlalchemy.sql import text as sql_text

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")
UnsignedAutoIncrementBigInteger = (
    BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)


def _medium_text():
    """JSON storage per the DDL: TEXT on SQLite, mediumtext on MySQL."""
    return Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


def _base_axes():
    """Fresh copies of the four shared axis columns.

    A Column instance cannot be shared between two mapped classes, so each
    model takes a fresh tuple from this helper rather than reusing objects.
    """
    return (
        Column(String(20), nullable=False, default=get_current_env),
        Column(String(64), nullable=False, server_default="teamclaw"),
        Column(DateTime, nullable=False, server_default=func.now()),
        Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )


class ContextCollabColletModel(Base):
    """One context collaboration row matching the deployed physical schema.

    ``context_spec`` is the only JSON storage envelope for L4 scope, audit
    metadata, resource bindings, creation facts and the root-Task pointer.
    The legacy ``scope``, ``collet_metadata``, ``resources``,
    ``creation_facts`` and ``root_task_ref`` columns are deliberately absent:
    the deployed table never received that migration. ``current_snapshot``
    remains a real legacy compatibility column and is nullable.
    """

    __tablename__ = "ac_context_collab_collet"

    id = Column(
        UnsignedAutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    collab_context_id = Column(String(128), nullable=False)
    # Product-only facts. NULL preserves independent L4 rows. The owning L6
    # schema module attaches indexes without introducing an L4 -> L6 import;
    # product relationships are validated by the business repository, not FKs.
    content = Column(_medium_text(), nullable=True)
    status = Column(
        String(32).with_variant(mysql.VARCHAR(32, collation="utf8mb4_bin"), "mysql"),
        nullable=True,
    )
    product_profile_id = Column(AutoIncrementBigInteger, nullable=True)
    current_rollout_id = Column(AutoIncrementBigInteger, nullable=True)
    # 创建主体引用(与初始 task.created 事件的 actor_ref 同值);可空:
    # 外部部分行可以没有;P1 创建路径恒写。
    actor_ref = Column(String(2048), nullable=True)
    # Complete storage envelope; see ``context_spec_codec.py`` for its typed
    # decoder. It is the physical replacement for the never-applied split
    # columns, rather than a compatibility projection.
    context_spec = Column(_medium_text(), nullable=False)
    # Legacy-only compatibility column. New rows write NULL; taskful rows keep
    # their typed state in ac_context_collab_task and derive response views.
    current_snapshot = Column(_medium_text(), nullable=True)
    # Deprecated SQL compatibility column; not read/written by the domain.
    # The current event is derived from accepted Event order at query time.
    current_event_id = Column(String(128), nullable=True)
    context_version = Column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=sql_text("1"),
    )
    env, avernet_tenant, gmt_create, gmt_modified = _base_axes()

    __table_args__ = (
        Index(
            "uk_context_collab_collet_context",
            "avernet_tenant",
            "env",
            "collab_context_id",
            unique=True,
        ),
        Index(
            "idx_context_collab_collet_modified",
            "avernet_tenant",
            "env",
            "gmt_modified",
        ),
    )


register_avernet_tenant_guard(ContextCollabColletModel)


class ContextCollabEventModel(Base):
    """One accepted event in a context's append-only event DAG.

    ``parent_event`` is NULL for the main-line creation event and otherwise
    references an existing event in the same context; acceptance order is the
    auto-increment ``id``. ``actor_ref`` / ``visibility`` / ``payload`` /
    ``context_version_after`` are nullable so out-of-band writers may record
    partial rows; the P1 append path always writes them. ``gmt_modified``
    follows the shared axis pattern: the append path writes it equal to
    ``gmt_create`` and the DDL keeps the ON UPDATE default for any future
    out-of-band rewrite.
    """

    __tablename__ = "ac_context_collab_event"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    collab_context_id = Column(String(128), nullable=False)
    event_id = Column(String(128), nullable=False)
    collet_rollout_id = Column(AutoIncrementBigInteger, nullable=True)
    collet_stage_rollout_id = Column(AutoIncrementBigInteger, nullable=True)
    task_ref = Column(String(128), nullable=True)
    task_version_after = Column(BigInteger, nullable=True)
    parent_event = Column(String(128), nullable=True)
    event_kind = Column(String(128), nullable=False)
    # 可空(spec 作者裁决):允许外部写入的部分行;P1 追加路径仍必写白名单值。
    primitive_type = Column(String(64), nullable=True)
    # 可空列(spec 作者裁决):允许外部写入部分字段的事件行;P1 追加路径
    # 仍总是写满这些值,读路径对 NULL 容忍(visibility=NULL 按 private 处理,
    # primitive_type=NULL 仅保留事件展示,不代表独立 Task)。
    # JSON 仅几十字节,varchar(2048) 足够,不再用 mediumtext。
    actor_ref = Column(String(2048), nullable=True)
    visibility = Column(String(32), nullable=True)
    payload = Column(_medium_text(), nullable=True)
    context_version_after = Column(BigInteger, nullable=True)
    occurred_at = Column(TIMESTAMP, nullable=False)
    # Append-only table, but gmt_modified still mirrors the shared axis
    # pattern so an out-of-band rewrite keeps the audit trail consistent.
    env, avernet_tenant, gmt_create, gmt_modified = _base_axes()

    __table_args__ = (
        Index(
            "uk_context_collab_event",
            "avernet_tenant",
            "env",
            "collab_context_id",
            "event_id",
            unique=True,
        ),
        Index(
            "idx_context_collab_event_parent",
            "avernet_tenant",
            "env",
            "collab_context_id",
            "parent_event",
        ),
    )


register_avernet_tenant_guard(ContextCollabEventModel)


class ContextCollabTaskModel(Base):
    """Independent L4 Task revision; JSON contains the typed Task, not a view."""

    __tablename__ = "ac_context_collab_task"
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(128), nullable=False)
    collab_context_id = Column(String(128), nullable=False)
    parent_task_ref = Column(String(128), nullable=True)
    task_version = Column(BigInteger, nullable=False)
    task_data = Column(_medium_text(), nullable=False)
    env, avernet_tenant, gmt_create, gmt_modified = _base_axes()

    __table_args__ = (
        Index(
            "uk_context_collab_task", "avernet_tenant", "env", "task_id", unique=True
        ),
        Index(
            "idx_context_collab_task_context",
            "avernet_tenant",
            "env",
            "collab_context_id",
        ),
    )


register_avernet_tenant_guard(ContextCollabTaskModel)
