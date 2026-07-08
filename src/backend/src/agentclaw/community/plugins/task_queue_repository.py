"""Unified task-queue repository (prod OceanBase + local SQLite).

One ORM implementation behind ``TaskQueueRepositoryProtocol``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local) — the same
"unified repository" pattern as ``plugins/bot_publish_repository.py``. No
``@plugin_impl`` decorator: it is mode-agnostic and bound directly in DI.

**The database owns all timing.** Callers pass durations; this repository turns
them into timestamps with the DB clock (``now()``) and evaluates every
eligibility / lease / deadline comparison DB-side. The one dialect-specific
detail is "now plus N seconds" — :meth:`_now_plus` renders the right SQL for
SQLite vs MySQL/OceanBase. No Python ``now`` is ever used for scheduling, so
clock skew between worker pods cannot affect claim/lease/deadline decisions.

**Idempotency** is enforced in :meth:`claim_batch`: per-row compare-and-swap
whose WHERE reproduces eligibility, so across N racing workers each row is won
by exactly one (the DB serializes the row write; the rest see it already
``RUNNING`` with a live lease → ``affected == 0``). No ``SELECT ... FOR
UPDATE``. The same CAS shape guards ``complete`` / ``reschedule`` / ``fail`` on
``claimed_by == worker_id AND status == RUNNING``.

``gmt_created`` / ``gmt_modified`` are left entirely to the database (column
default + ``ON UPDATE CURRENT_TIMESTAMP``); this body never sets them.
"""
from __future__ import annotations

import json
from typing import List, Optional

from injector import inject
from sqlalchemy import and_, func, or_, text
from sqlalchemy.sql.elements import ColumnElement

from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.types import TaskRecord, TaskStatus
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class TaskQueueRepository:
    """Unified ORM ``TaskQueueRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = TaskQueueModel

    # ── DB-clock helpers ────────────────────────────────────────────────

    @staticmethod
    def _now_plus(db, seconds: float) -> ColumnElement:
        """A SQL expression for ``now() + seconds``, evaluated DB-side.

        The only dialect-specific bit in this repository: SQLite and
        MySQL/OceanBase spell interval arithmetic differently. ``seconds`` is
        coerced to a whole number (the DB clock is second-granular) and inlined
        as a literal int — never user input, so no injection surface.
        """
        n = int(round(seconds))
        if n == 0:
            return func.now()
        dialect = db.bind.dialect.name
        if dialect == "sqlite":
            return func.datetime(func.now(), text(f"'+{n} seconds'"))
        # mysql / oceanbase
        return func.date_add(func.now(), text(f"INTERVAL {n} SECOND"))

    def _eligible(self, env: str):
        """Eligibility predicate: due, env-scoped, and either PENDING or a
        RUNNING row whose lease has expired (abandoned by a crashed worker).
        All compared against the DB clock."""
        now = func.now()
        return and_(
            self.Model.env == env,
            self.Model.run_at <= now,
            or_(
                self.Model.status == TaskStatus.PENDING.value,
                and_(
                    self.Model.status == TaskStatus.RUNNING.value,
                    self.Model.lease_expires_at <= now,
                ),
            ),
        )

    def _holder_filter(self, task_id: int, worker_id: str):
        return and_(
            self.Model.id == task_id,
            self.Model.claimed_by == worker_id,
            self.Model.status == TaskStatus.RUNNING.value,
        )

    # ── enqueue (plain INSERT — never an upsert) ────────────────────────

    def enqueue(
        self,
        *,
        task_type: str,
        payload: dict,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
    ) -> TaskRecord:
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._db.orm_session() as db:
            row = self.Model(
                task_type=task_type,
                payload=payload_json,
                status=TaskStatus.PENDING.value,
                run_at=self._now_plus(db, delay_seconds),
                deadline_at=self._now_plus(db, deadline_seconds),
                attempts=0,
                env=env,
            )
            db.add(row)
            db.flush()
            new_id = row.id
            logger.info(
                "[task_queue.enqueue] type=%s id=%s delay=%ss deadline=%ss",
                task_type,
                new_id,
                delay_seconds,
                deadline_seconds,
            )
        record = self.get_by_id(new_id)
        assert record is not None  # just inserted
        return record

    # ── claim (the single-winner CAS) ───────────────────────────────────

    def claim_batch(
        self,
        *,
        worker_id: str,
        env: str,
        limit: int,
        lease_seconds: int,
    ) -> List[TaskRecord]:
        if limit <= 0:
            return []
        won: List[TaskRecord] = []
        with self._db.orm_session() as db:
            # 1) Read candidates (cheap, no lock). Oldest run_at first so the
            #    queue drains roughly FIFO and starvation is bounded.
            candidate_ids = [
                row_id
                for (row_id,) in db.query(self.Model.id)
                .filter(self._eligible(env))
                .order_by(self.Model.run_at.asc())
                .limit(limit)
                .all()
            ]

            for task_id in candidate_ids:
                # 2a) Claim it — but only while still eligible AND within
                #     deadline. The WHERE reproduces eligibility, so only the
                #     worker whose UPDATE lands first wins the row.
                claimed = (
                    db.query(self.Model)
                    .filter(
                        self.Model.id == task_id,
                        self._eligible(env),
                        self.Model.deadline_at > func.now(),
                    )
                    .update(
                        {
                            self.Model.status: TaskStatus.RUNNING.value,
                            self.Model.claimed_by: worker_id,
                            self.Model.lease_expires_at: self._now_plus(
                                db, lease_seconds
                            ),
                            self.Model.attempts: self.Model.attempts + 1,
                        },
                        synchronize_session=False,
                    )
                )
                if claimed == 1:
                    row = (
                        db.query(self.Model)
                        .filter(self.Model.id == task_id)
                        .first()
                    )
                    if row is not None:
                        won.append(row.to_record())
                    continue

                # 2b) Didn't win it as RUNNING. If that's because the deadline
                #     has passed, retire it as TIMED_OUT (terminal) so it stops
                #     being a candidate. (If instead another worker just claimed
                #     it, this matches nothing — harmless.)
                db.query(self.Model).filter(
                    self.Model.id == task_id,
                    self._eligible(env),
                    self.Model.deadline_at <= func.now(),
                ).update(
                    {
                        self.Model.status: TaskStatus.TIMED_OUT.value,
                        self.Model.last_error: "deadline elapsed before execution",
                        self.Model.claimed_by: None,
                        self.Model.lease_expires_at: None,
                    },
                    synchronize_session=False,
                )
        if won:
            logger.info(
                "[task_queue.claim] worker=%s claimed %d/%d candidates",
                worker_id,
                len(won),
                len(candidate_ids),
            )
        return won

    # ── outcome transitions (CAS-guarded on the holder) ─────────────────

    def _apply_to_holder(self, task_id: int, worker_id: str, values: dict) -> bool:
        with self._db.orm_session() as db:
            affected = (
                db.query(self.Model)
                .filter(self._holder_filter(task_id, worker_id))
                .update(values, synchronize_session=False)
            )
        return affected == 1

    def complete(self, *, task_id: int, worker_id: str) -> bool:
        return self._apply_to_holder(
            task_id,
            worker_id,
            {
                self.Model.status: TaskStatus.SUCCEEDED.value,
                self.Model.claimed_by: None,
                self.Model.lease_expires_at: None,
            },
        )

    def reschedule(
        self,
        *,
        task_id: int,
        worker_id: str,
        delay_seconds: float,
        error: Optional[str] = None,
    ) -> bool:
        with self._db.orm_session() as db:
            next_run = self._now_plus(db, delay_seconds)

            # Common case: the new run_at is within the deadline → re-pend.
            values = {
                self.Model.status: TaskStatus.PENDING.value,
                self.Model.run_at: next_run,
                self.Model.claimed_by: None,
                self.Model.lease_expires_at: None,
            }
            if error is not None:
                values[self.Model.last_error] = error
            rescheduled = (
                db.query(self.Model)
                .filter(
                    self._holder_filter(task_id, worker_id),
                    self._now_plus(db, delay_seconds) < self.Model.deadline_at,
                )
                .update(values, synchronize_session=False)
            )
            if rescheduled == 1:
                return True

            # The retry/poll would land past the deadline → give up DB-side.
            timed_out = (
                db.query(self.Model)
                .filter(
                    self._holder_filter(task_id, worker_id),
                    self._now_plus(db, delay_seconds) >= self.Model.deadline_at,
                )
                .update(
                    {
                        self.Model.status: TaskStatus.TIMED_OUT.value,
                        self.Model.last_error: (error or "deadline elapsed"),
                        self.Model.claimed_by: None,
                        self.Model.lease_expires_at: None,
                    },
                    synchronize_session=False,
                )
            )
        return timed_out == 1

    def fail(self, *, task_id: int, worker_id: str, error: str) -> bool:
        return self._apply_to_holder(
            task_id,
            worker_id,
            {
                self.Model.status: TaskStatus.FAILED.value,
                self.Model.last_error: error,
                self.Model.claimed_by: None,
                self.Model.lease_expires_at: None,
            },
        )

    # ── diagnosis / tests ───────────────────────────────────────────────

    def get_by_id(self, task_id: int) -> Optional[TaskRecord]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == task_id)
                .first()
            )
            return row.to_record() if row else None

    def list_by_status(self, *, status: TaskStatus, env: str) -> List[TaskRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.status == status.value,
                    self.Model.env == env,
                )
                .order_by(self.Model.run_at.asc())
                .all()
            )
            return [r.to_record() for r in rows]
