"""ORM models for the context collaboration Collet/Event tables.

Phase 1 of the BBS context collaboration protocol persists exactly two tables
(spec §9.5): one aggregate snapshot row per collaboration context, plus the
append-only event DAG that rewrites that snapshot. The SQL source of truth for
production is ``../sql/2026_11_11_context_collab.sql`` (OceanBase MySQL mode);
tests build these models on SQLite via ``Base.metadata.create_all``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
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
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


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
    """One collaboration context: the aggregate snapshot row.

    ``context_spec`` / ``current_snapshot`` hold the JSON projections of the
    create request and the current objective/status/executor/progress; the
    event DAG in ``ac_context_collab_event`` rewrites the snapshot and bumps
    ``context_version`` per accepted event.
    """

    __tablename__ = "ac_context_collab_collet"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    collab_context_id = Column(String(128), nullable=False)
    context_spec = Column(_medium_text(), nullable=False)
    current_snapshot = Column(_medium_text(), nullable=False)
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
    auto-increment ``id``. The DDL defines no ``gmt_modified`` because rows
    are never rewritten.
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
    parent_event = Column(String(128), nullable=True)
    event_kind = Column(String(128), nullable=False)
    primitive_type = Column(String(64), nullable=False)
    actor_ref = Column(_medium_text(), nullable=False)
    visibility = Column(String(32), nullable=False)
    payload = Column(_medium_text(), nullable=False)
    context_version_after = Column(BigInteger, nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    # Append-only table: the DDL carries no gmt_modified, so take only the
    # first three axis columns (env, avernet_tenant, gmt_create).
    env, avernet_tenant, gmt_create = _base_axes()[:3]

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
