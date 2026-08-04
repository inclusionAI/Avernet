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

**Claim-time idempotency** is enforced in :meth:`claim_batch`: per-row
compare-and-swap whose WHERE reproduces eligibility, so across N racing workers
each row is won by exactly one (the DB serializes the row write; the rest see it
already ``RUNNING`` with a live lease → ``affected == 0``). No ``SELECT ... FOR
UPDATE``. The same CAS shape guards ``complete`` / ``reschedule`` / ``fail`` on
``claimed_by == worker_id AND status == RUNNING``.

**Enqueue-time idempotency** is opt-in and *active-only*: at most one **live**
task per ``idempotency_key`` within an ``(env, task_type)``. A ``None`` key opts
out entirely and can never collide (engines treat NULLs as distinct in a unique
index) — so un-keyed callers behave exactly as they always have.

The scope is active-only rather than all-time because several call sites
legitimately re-enqueue the same logical key after the previous task went
terminal — a publish poll runs once per stage, a retry re-runs a failed stage, a
bot restarts more than once, and skills-pool reconcile is level-triggered. An
all-time key would silently swallow those.

It is enforced by a second column: ``active_idempotency_key`` mirrors
``idempotency_key`` while the task is live and is set to ``NULL`` by **every**
terminal transition, which releases the key. MySQL/OceanBase have no partial
indexes, so nulling a plain column is the portable way to say "unique among live
rows only". There are exactly four such transitions, and each nulls the key
*inside the same UPDATE* as its status change — so "terminal but key still held"
is unrepresentable and there is no ordering window to get wrong:

1. :meth:`claim_batch` — a past-deadline candidate retired ``TIMED_OUT``.
2. :meth:`complete` — ``SUCCEEDED``.
3. :meth:`reschedule` — a retry that would overshoot the deadline → ``TIMED_OUT``.
4. :meth:`fail` — ``FAILED``.

:meth:`reschedule`'s ``PENDING`` branch, :meth:`claim_batch`'s ``RUNNING`` claim,
and :meth:`renew_lease` deliberately do **not** clear it: those tasks are still
live and keep holding their key.

``gmt_created`` / ``gmt_modified`` are left entirely to the database (column
default + ``ON UPDATE CURRENT_TIMESTAMP``); this body never sets them.
"""
from __future__ import annotations

import json
from typing import List, Optional

from injector import inject
from sqlalchemy import and_, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.types import EnqueueResult, TaskRecord, TaskStatus
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()

#: The unique index backing active-only enqueue dedup. Named here because the
#: two engines report a violation of it differently and both spellings must be
#: recognised — see :func:`_is_active_idem_conflict`.
_ACTIVE_IDEM_INDEX = "uk_env_task_type_active_idem"

#: How many times :meth:`TaskQueueRepository.enqueue` will try to insert a keyed
#: task. A second attempt is only ever reached when the conflicting row went
#: terminal (releasing the key) between the failed INSERT and the re-SELECT, so
#: two is enough to cover the race without risking an unbounded loop.
_KEYED_INSERT_ATTEMPTS = 2


def _is_active_idem_conflict(exc: IntegrityError) -> bool:
    """Is this ``IntegrityError`` a violation of the active-idempotency index?

    Scoped deliberately: a blanket ``except IntegrityError`` would turn an
    unrelated constraint violation into a bogus "duplicate" and hand the caller
    somebody else's row.

    The two engines name the violation differently, so both spellings are
    matched:

    - MySQL / OceanBase report the **index**::

          Duplicate entry 'dev-demo-k1' for key 'uk_env_task_type_active_idem'

    - SQLite reports the **columns**::

          UNIQUE constraint failed: ac_task_queue.env, ac_task_queue.task_type,
          ac_task_queue.active_idempotency_key

    A pure function over the exception so it is unit-testable against both
    message forms without a MySQL instance.
    """
    message = str(getattr(exc, "orig", None) or exc)
    if _ACTIVE_IDEM_INDEX in message:
        return True
    # SQLite names the columns instead of the index. active_idempotency_key
    # appears in no other constraint on this table, so it alone identifies it.
    return "UNIQUE constraint failed" in message and "active_idempotency_key" in message


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

    # ── enqueue (INSERT; deduped against live rows when keyed) ──────────

    def _insert(
        self,
        *,
        task_type: str,
        payload_json: str,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
        idempotency_key: Optional[str],
    ) -> TaskRecord:
        """INSERT one PENDING row and return it. Raises ``IntegrityError`` when
        a keyed insert loses to a live holder of the same key."""
        with self._db.orm_session() as db:
            row = self.Model(
                task_type=task_type,
                payload=payload_json,
                status=TaskStatus.PENDING.value,
                run_at=self._now_plus(db, delay_seconds),
                deadline_at=self._now_plus(db, deadline_seconds),
                attempts=0,
                env=env,
                idempotency_key=idempotency_key,
                # Mirrors the key while the task is live; nulled on terminal.
                active_idempotency_key=idempotency_key,
            )
            db.add(row)
            db.flush()
            new_id = row.id
        record = self.get_by_id(new_id)
        assert record is not None  # just inserted
        return record

    def _find_active_by_key(
        self, *, env: str, task_type: str, idempotency_key: str
    ) -> Optional[TaskRecord]:
        """The **live** holder of ``idempotency_key``, or ``None``.

        Filters on ``active_idempotency_key``, so terminal rows — which released
        the key — are invisible here by construction; there is no need to
        restate the status predicate.
        """
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.env == env,
                    self.Model.task_type == task_type,
                    self.Model.active_idempotency_key == idempotency_key,
                )
                .first()
            )
            return row.to_record() if row else None

    def enqueue(
        self,
        *,
        task_type: str,
        payload: dict,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
        idempotency_key: Optional[str] = None,
    ) -> EnqueueResult:
        payload_json = json.dumps(payload, ensure_ascii=False)
        insert_kwargs = dict(
            task_type=task_type,
            payload_json=payload_json,
            delay_seconds=delay_seconds,
            deadline_seconds=deadline_seconds,
            env=env,
            idempotency_key=idempotency_key,
        )

        # Un-keyed: the caller opted out of dedup, so this stays a plain INSERT
        # with none of the conflict machinery on the path.
        if idempotency_key is None:
            record = self._insert(**insert_kwargs)
            self._log_enqueued(record, delay_seconds, deadline_seconds, created=True)
            return EnqueueResult(record, True)

        # Keyed: try-insert, and on a conflict with *this* index hand back the
        # live holder. ``orm_session()`` rolls back and closes on exception
        # (and the corp engine runs at AUTOCOMMIT), so the failed INSERT leaves
        # nothing to clean up and the lookup below runs in a fresh session.
        for _ in range(_KEYED_INSERT_ATTEMPTS):
            try:
                record = self._insert(**insert_kwargs)
            except IntegrityError as exc:
                if not _is_active_idem_conflict(exc):
                    raise  # unrelated constraint — never read as a duplicate
                existing = self._find_active_by_key(
                    env=env, task_type=task_type, idempotency_key=idempotency_key
                )
                if existing is not None:
                    logger.info(
                        "[task_queue.enqueue] type=%s joined existing id=%s key=%s",
                        task_type,
                        existing.id,
                        idempotency_key,
                    )
                    return EnqueueResult(existing, False)
                # The holder reached a terminal state between our INSERT and
                # this lookup, releasing the key — it is free again, so retry.
                continue
            self._log_enqueued(record, delay_seconds, deadline_seconds, created=True)
            return EnqueueResult(record, True)

        raise RuntimeError(
            "[task_queue.enqueue] could not insert or resolve a holder for "
            f"key={idempotency_key!r} type={task_type} env={env} after "
            f"{_KEYED_INSERT_ATTEMPTS} attempts"
        )

    @staticmethod
    def _log_enqueued(
        record: TaskRecord, delay_seconds: int, deadline_seconds: int, *, created: bool
    ) -> None:
        logger.info(
            "[task_queue.enqueue] type=%s id=%s delay=%ss deadline=%ss key=%s created=%s",
            record.task_type,
            record.id,
            delay_seconds,
            deadline_seconds,
            record.idempotency_key,
            created,
        )

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
                        # Terminal → release the dedup key (see module docstring).
                        self.Model.active_idempotency_key: None,
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
                # Terminal → release the dedup key (see module docstring).
                self.Model.active_idempotency_key: None,
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
                        # Terminal → release the dedup key (see module docstring).
                        self.Model.active_idempotency_key: None,
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
                # Terminal → release the dedup key (see module docstring).
                self.Model.active_idempotency_key: None,
            },
        )

    def renew_lease(self, *, task_id: int, worker_id: str, lease_seconds: int) -> bool:
        # Holder-guarded lease extension: keep RUNNING + claimed_by, only push
        # lease_expires_at forward (DB clock). Not via _apply_to_holder because the
        # new timestamp must be computed inside the session (_now_plus).
        with self._db.orm_session() as db:
            affected = (
                db.query(self.Model)
                .filter(self._holder_filter(task_id, worker_id))
                .update(
                    {self.Model.lease_expires_at: self._now_plus(db, lease_seconds)},
                    synchronize_session=False,
                )
            )
        return affected == 1

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
