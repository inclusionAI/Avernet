"""SQLAlchemy ORM model for the ``ac_task_queue`` table.

One generic table backs the whole component. It shares the canonical
``agentclaw.community.core.base.Base`` so the local-mode ``create_all`` bootstrap
builds it alongside every other table; the prod (OceanBase) table is created
manually and must mirror this definition — including ``gmt_create`` /
``gmt_modified`` as ``DEFAULT CURRENT_TIMESTAMP [ON UPDATE CURRENT_TIMESTAMP]``
so the database manages those audit columns.
"""
import json

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task_queue.types import TaskRecord, TaskStatus
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY
# KEY". BigInteger renders as "BIGINT" on SQLite, which breaks autoincrement.
# with_variant() keeps BIGINT on MySQL/OceanBase but uses INTEGER on SQLite.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class TaskQueueModel(Base):
    """ORM model for ``ac_task_queue`` — one row per unit of background work."""

    __tablename__ = "ac_task_queue"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )

    # ── identity / payload ──────────────────────────────────────────────
    task_type = Column(String(100), nullable=False, comment="handler registry key")
    payload = Column(Text, nullable=False, comment="JSON string; deserialized in to_record()")

    # ── scheduling / claim state ────────────────────────────────────────
    status = Column(
        String(20),
        nullable=False,
        default=TaskStatus.PENDING.value,
        comment="PENDING / RUNNING / SUCCEEDED / FAILED / TIMED_OUT",
    )
    run_at = Column(
        DateTime,
        nullable=False,
        comment="next-eligible time (DB clock); claim requires run_at <= now()",
    )
    claimed_by = Column(
        String(128),
        nullable=True,
        comment="worker id of the current holder; null when not RUNNING",
    )
    lease_expires_at = Column(
        DateTime,
        nullable=True,
        comment="claim lease deadline (DB clock); expired => reclaimable",
    )
    attempts = Column(
        Integer,
        nullable=False,
        default=0,
        comment="incremented on each claim; diagnostic only (not the give-up rule)",
    )
    last_error = Column(Text, nullable=True, comment="last failure / timeout message")
    deadline_at = Column(
        DateTime,
        nullable=False,
        comment="give-up time from first enqueue (DB clock); a task always has one",
    )

    # ── env scoping / audit ─────────────────────────────────────────────
    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="环境标识: prod/pre/dev; all queries filter by env",
    )
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="first-enqueue audit time")
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="audit; set DB-side on every CAS UPDATE",
    )

    __table_args__ = (
        # Claim scan: WHERE env=? AND status=? AND run_at<=? ORDER BY run_at.
        Index("idx_env_status_run_at", "env", "status", "run_at"),
        # Reclaim scan: env-scoped lookup of expired RUNNING leases.
        Index("idx_env_lease_expires_at", "env", "lease_expires_at"),
    )

    def to_record(self) -> TaskRecord:
        """Project the row into an immutable :class:`TaskRecord`."""
        return TaskRecord(
            id=self.id,
            task_type=self.task_type,
            payload=json.loads(self.payload),
            status=TaskStatus(self.status),
            deadline_at=self.deadline_at,
            run_at=self.run_at,
            claimed_by=self.claimed_by,
            lease_expires_at=self.lease_expires_at,
            attempts=self.attempts,
            last_error=self.last_error,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
