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
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.task_queue.types import TaskRecord, TaskStatus
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY
# KEY". BigInteger renders as "BIGINT" on SQLite, which breaks autoincrement.
# with_variant() keeps BIGINT on MySQL/OceanBase but uses INTEGER on SQLite.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")

def _binary_string(length: int):
    """``VARCHAR(length)`` that compares **byte-for-byte** on MySQL/OceanBase.

    Both engines default to a ``utf8mb4_*_ci`` collation, under which
    ``publish:Bot-A:poll`` and ``publish:bot-a:poll`` are *equal* in a unique
    index — letting one caller's row silently absorb another's. SQLite already
    compares BINARY, so the divergence is invisible to the test suite; pinning
    ``utf8mb4_bin`` on the MySQL variant makes the two engines agree.
    ``with_variant`` leaves SQLite's plain ``VARCHAR`` alone (it has no such
    collation and would reject the DDL).

    Note ``utf8mb4_bin`` is itself **PAD SPACE**: it settles case but not
    trailing spaces. Every column using this type therefore also rejects
    trailing whitespace in Python — see ``_validate_idempotency_key`` for keys
    and ``HandlerRegistry.register`` for task types.
    """
    return String(length).with_variant(
        mysql.VARCHAR(length, collation="utf8mb4_bin"), "mysql"
    )


#: Dedup keys are caller-supplied and stored verbatim, so they must compare
#: byte-for-byte.
IdempotencyKeyString = _binary_string(190)

#: ``task_type`` is the third column of the dedup unique index, so its collation
#: decides key scope just as much as the key column's does. It is a registry key
#: matched exactly in Python, and the database must agree: otherwise ``Job`` and
#: ``job`` are two handlers but one index entry, and a keyed enqueue for one
#: joins the other's live task. ``HandlerRegistry`` also refuses to register two
#: types that fold together, but that check is process-local — it cannot see a
#: row written by another version during a rolling deploy, which is why the
#: scope is enforced in the schema rather than only in the application.
TaskTypeString = _binary_string(100)


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
    task_type = Column(TaskTypeString, nullable=False, comment="handler registry key")
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

    # ── enqueue idempotency (opt-in, active-only) ───────────────────────
    # Two columns on purpose. ``idempotency_key`` is the durable audit value:
    # written once at enqueue, never cleared, so "which task handled key X?"
    # stays answerable after the task finishes. ``active_idempotency_key`` is
    # the enforcement value: equal to it while the task is live, NULLed on every
    # terminal transition to release the key. MySQL/OceanBase have no partial
    # indexes, so nulling a plain column is the portable way to express
    # "unique among live rows only".
    idempotency_key = Column(
        IdempotencyKeyString,
        nullable=True,
        comment="caller-supplied enqueue dedup key; NULL = opted out. Audit only",
    )
    active_idempotency_key = Column(
        IdempotencyKeyString,
        nullable=True,
        comment="enforcement copy of idempotency_key; NULLed on terminal transitions",
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
        # Active-only enqueue dedup: at most one *live* task per key within an
        # (env, task_type). Terminal rows null their active key and drop out.
        # A NULL active key is the opt-out — both MySQL/OceanBase and SQLite
        # treat NULLs as distinct in a unique index, so un-keyed enqueues never
        # collide with each other. That property is relied upon, not incidental.
        #
        # task_type and active_idempotency_key both pin utf8mb4_bin, because a
        # unique index is only as precise as its least precise column: under the
        # default ci collation 'Job' and 'job' would be one entry, so a keyed
        # enqueue for one would join the other's live task.
        #
        # env deliberately keeps the table default. Unlike task_type it is
        # compared by _eligible() and carries two other indexes, so altering it
        # would change pre-existing claim/reclaim behaviour — a far wider change
        # than the risk, given env comes from deployment config rather than
        # per-call input. task_type is compared in SQL only by
        # _find_active_by_key and appears in no other index, so pinning it costs
        # nothing elsewhere.
        #
        # utf8mb4_bin is PAD SPACE, so it settles case but not trailing spaces;
        # enqueue and HandlerRegistry.register reject values carrying any.
        #
        # ONE REQUIREMENT THIS DECLARATION CANNOT EXPRESS: on OceanBase the
        # index must be created GLOBAL, as every other unique index in the
        # deployment is. SQLAlchemy has no way to render that, so what follows
        # is a plain unique index and the modifier lives only in README.md's
        # Provisioning section. A partition-local index would allow the same
        # active key once per partition and defeat dedup, so anyone provisioning
        # the table by hand has to know it — do not read this declaration as the
        # complete specification of the index.
        Index(
            "uk_env_task_type_active_idempotency_key",
            "env",
            "task_type",
            "active_idempotency_key",
            unique=True,
        ),
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
            idempotency_key=self.idempotency_key,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
