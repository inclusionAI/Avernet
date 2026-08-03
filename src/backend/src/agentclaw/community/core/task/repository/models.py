"""SQLAlchemy models for the task aggregate (Phase 1.1, plan §1.1).

Three tables on the canonical ``agentclaw.community.core.base.Base``:

- ``ac_task`` — the aggregate snapshot (spec + status + loop_round + latest_seq).
  PK ``id`` is the task id (string, application-assigned). ``spec_json`` carries
  the serialized :class:`TaskSpec` (intake/plan face); ``execution_graph_json``
  carries the serialized :class:`TaskExecutionGraph` (runtime face) — kept here
  on the snapshot row for fast reads (the event log is source of truth). env +
  status indexes for list queries.
- ``ac_task_event`` — the append-only event log. ``gmt_create`` only (no
  ``gmt_modified``); ``(env, task_id, seq)`` unique + index for the single-writer
  seq watermark. ``payload_json`` carries the event payload.
- ``ac_task_execution_graph`` — optional read-optimized projection of the
  execution graph (``graph`` Text = serialized ``TaskExecutionGraph`` +
  ``version`` Int for optimistic updates). Materialized from the event log;
  ``ac_task`` also carries the graph inline so this table is an auxiliary
  large-payload split, not the primary store.

``AutoIncrementBigInteger.with_variant(Integer, "sqlite")`` keeps autoincrement
working on both SQLite (local/CI) and OceanBase/MySQL (prod).

Avernet rules: ``from __future__ import annotations``; no bare SQL (ORM only);
``Optional[T]`` not ``T | None``.
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
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class AcTaskModel(Base):
    """Snapshot row for the :class:`Task` aggregate (spec + runtime face)."""

    __tablename__ = "ac_task"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(128), nullable=False, unique=True)
    env = Column(String(64), nullable=False, default="dev")
    user_id = Column(String(128), nullable=False, default="")
    source = Column(String(32), nullable=False, default="api")
    status = Column(String(32), nullable=False, default="drafting")  # 对齐 GraphStatus.DRAFTING(graph.status 的查询镜像)
    loop_round = Column(Integer, nullable=False, default=0)
    spec_json = Column(Text, nullable=True)
    execution_graph_json = Column(Text, nullable=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_ac_task_env_status", "env", "status"),
        Index("idx_ac_task_env_user", "env", "user_id"),
        Index("idx_ac_task_env_uuid", "env", "task_id"),
    )


class AcTaskEventModel(Base):
    """Append-only event log; the single writer of the monotonic ``seq``.

    No ``gmt_modified`` — events are immutable once appended.
    """

    __tablename__ = "ac_task_event"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    env = Column(String(64), nullable=False, default="dev")
    task_id = Column(String(128), nullable=False)
    seq = Column(Integer, nullable=False)
    kind = Column(String(64), nullable=False)
    reported = Column(Integer, nullable=False, default=0)
    payload_json = Column(Text, nullable=True)
    gmt_create = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint("env", "task_id", "seq", name="uk_ac_task_event_seq"),
        Index("idx_ac_task_event_env_task_seq", "env", "task_id", "seq"),
    )


class AcTaskExecutionGraphModel(Base):
    """Auxiliary read-optimized projection of the execution graph.

    ``graph`` = serialized :class:`TaskExecutionGraph``; ``version`` is an
    optimistic-update counter. The authoritative graph is folded from the event
    log; this row is a materialized large-payload split so the ``ac_task`` row
    stays narrow for list queries.
    """

    __tablename__ = "ac_task_execution_graph"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    env = Column(String(64), nullable=False, default="dev")
    task_id = Column(String(128), nullable=False)
    graph = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    gmt_create = Column(DateTime, nullable=False, default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("env", "task_id", name="uk_ac_task_execution_graph_task"),
        Index("idx_ac_task_execution_graph_env_task", "env", "task_id"),
    )


__all__ = [
    "AcTaskEventModel",
    "AcTaskExecutionGraphModel",
    "AcTaskModel",
]