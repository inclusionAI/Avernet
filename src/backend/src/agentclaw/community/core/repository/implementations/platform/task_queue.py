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

**App scoping.** The table is shared with a second, independently deployed
backend, so every row names its owning ``app`` and every statement that *selects
work* matches it: the claim scan and its CAS, the deadline retirement inside that
scan, the enqueue dedup lookups, and ``list_by_status``. Callers pass ``app``
alongside ``env`` and take both from deployment config, never from a per-call
argument. Without it each fleet claims the other's rows and fails them for a
``task_type`` its registry never saw. ``get_by_id`` is deliberately unscoped —
it is a primary-key read used for diagnosis and for reading back an insert this
process just made.

**Enqueue-time idempotency** is opt-in and *active-only*: at most one **live**
task per ``idempotency_key`` within an ``(env, app, task_type)``. A ``None`` key
opts out entirely and can never collide (engines treat NULLs as distinct in a
unique index) — so un-keyed callers behave exactly as they always have.

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

from typing import List, Optional

from injector import inject
from sqlalchemy import and_, func, or_, text
from sqlalchemy.sql.elements import ColumnElement

from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.repository.pending_row_writer import (
    _ACTIVE_IDEM_INDEXES,
    _KEYED_INSERT_ATTEMPTS,
    _MAX_IDEMPOTENCY_KEY_LEN,
    _MAX_TASK_TYPE_LEN,
    TaskQueuePendingRowWriter,
    _is_active_idem_conflict,
)
from agentclaw.community.core.task_queue.types import (
    EnqueueResult,
    TaskRecord,
    TaskStatus,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.platform import TaskQueueRepositoryProtocol

logger = get_logger()

__all__ = [
    "TaskQueueRepository",
    "_ACTIVE_IDEM_INDEXES",
    "_KEYED_INSERT_ATTEMPTS",
    "_MAX_IDEMPOTENCY_KEY_LEN",
    "_MAX_TASK_TYPE_LEN",
    "_is_active_idem_conflict",
]


class TaskQueueRepository(
    TaskQueueRepositoryProtocol,
):
    """Unified ORM ``TaskQueueRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self.Model = TaskQueueModel
        self._pending_writer = TaskQueuePendingRowWriter()

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

    def _eligible(self, env: str, app: str):
        """Eligibility predicate: due, scoped to this ``(env, app)``, and either
        PENDING or a RUNNING row whose lease has expired (abandoned by a crashed
        worker). All compared against the DB clock.

        The ``app`` term is what keeps two backends sharing this table off each
        other's rows. It is part of the predicate rather than a pre-filter
        because the predicate is reused verbatim as the claim CAS's WHERE: a row
        that stopped being ours between the candidate read and the UPDATE must
        fail the CAS, not be taken anyway."""
        now = func.now()
        return and_(
            self.Model.env == env,
            self.Model.app == app,
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

    def _find_stranded_key_holder(
        self, *, env: str, app: str, task_type: str, idempotency_key: str
    ) -> Optional[int]:
        """Id of a **terminal** row of this app still holding ``idempotency_key``,
        or ``None``.

        This should always be ``None``: every terminal transition releases the
        key in the same ``UPDATE`` as the status change, so a terminal row
        holding one is an inconsistent row — written by a worker predating this
        feature, by hand, or by a future transition that forgets the release.

        It exists to tell two conflicts apart that are otherwise identical from
        the caller's side, and which want opposite responses. Both present as
        "the index rejected the insert, but there is no live holder":

        - **The key is genuinely free** — its holder went terminal inside the
          window between our INSERT and the lookup. Retrying is correct and
          will normally succeed.
        - **The key is stranded** — a terminal row still holds it. Retrying can
          *never* succeed, because the unique index does not care about status.

        Without the distinction both burn the same retry budget and end in the
        same generic error, so a permanent, actionable fault is reported as if
        it were bad luck. Told apart, the stranded case fails immediately and
        names the row to clear.
        """
        with self._db.orm_session() as session:
            return self._pending_writer._find_stranded_key_holder(
                session,
                env=env,
                app=app,
                task_type=task_type,
                idempotency_key=idempotency_key,
            )

    def enqueue(
        self,
        *,
        task_type: str,
        payload: dict,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
        app: str,
        idempotency_key: Optional[str] = None,
    ) -> EnqueueResult:
        with self._db.transactional_orm_session() as session:
            result = self._pending_writer.write_pending(
                session,
                task_type=task_type,
                payload=payload,
                delay_seconds=delay_seconds,
                deadline_seconds=deadline_seconds,
                env=env,
                app=app,
                idempotency_key=idempotency_key,
            )
        if result.created:
            self._log_enqueued(
                result.record,
                delay_seconds,
                deadline_seconds,
                created=True,
            )
        return result

    @staticmethod
    def _log_enqueued(
        record: TaskRecord, delay_seconds: int, deadline_seconds: int, *, created: bool
    ) -> None:
        logger.info(
            "[task_queue.enqueue] type=%s id=%s app=%s delay=%ss deadline=%ss key=%s created=%s",
            record.task_type,
            record.id,
            record.app,
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
        app: str,
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
                .filter(self._eligible(env, app))
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
                        self._eligible(env, app),
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
                    self._eligible(env, app),
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
                "[task_queue.claim] worker=%s app=%s claimed %d/%d candidates",
                worker_id,
                app,
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
        # Deliberately not app-scoped: an id is already unique across apps, and
        # this is both the read-back of an insert this process just made and the
        # diagnostic entry point for "what happened to task 71544?" — which is
        # asked precisely when the row's owner is what you are trying to find
        # out. ``TaskRecord.app`` carries it, so a caller that needs the scope
        # can still check it.
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == task_id)
                .first()
            )
            return row.to_record() if row else None

    def list_by_status(
        self, *, status: TaskStatus, env: str, app: str
    ) -> List[TaskRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(
                    self.Model.status == status.value,
                    self.Model.env == env,
                    self.Model.app == app,
                )
                .order_by(self.Model.run_at.asc())
                .all()
            )
            return [r.to_record() for r in rows]
