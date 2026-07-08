"""Repository interface for the task queue.

This is a **business** repository, so it lives in ``core/`` (not
``plugin_api/``). The concrete unified ORM implementation
(``plugins/task_queue_repository.py``) satisfies this Protocol structurally and
runs on SQLite (local) and OceanBase (prod) via the injected
``DatabasePlugin.orm_session()``.

**The database owns all timing.** Callers pass *durations* (delays / lease /
deadline, in seconds); the repository turns them into absolute timestamps using
the DB clock (``now()``), and every eligibility / lease / deadline comparison
is evaluated DB-side too. No Python-generated ``now`` ever crosses this
boundary — so clock skew between worker pods cannot affect claim, lease, or
deadline decisions.

**Claiming** is where idempotency is enforced: a row-level compare-and-swap
UPDATE whose predicate only matches an unclaimed (or lease-expired) row, so
across N racing workers each task is won by at most one. Past-deadline
candidates are marked ``TIMED_OUT`` instead of claimed. ``complete`` /
``reschedule`` / ``fail`` are CAS-guarded on ``claimed_by == worker_id AND
status == RUNNING`` so a worker that lost its lease cannot clobber a task
another worker took over.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from agentclaw.community.core.task_queue.types import TaskRecord, TaskStatus


@runtime_checkable
class TaskQueueRepositoryProtocol(Protocol):
    """Durable store for queued tasks with DB-level single-claimer semantics."""

    # ── enqueue ─────────────────────────────────────────────────────────
    def enqueue(
        self,
        *,
        task_type: str,
        payload: dict,
        delay_seconds: int,
        deadline_seconds: int,
        env: str,
    ) -> TaskRecord:
        """Persist a new ``PENDING`` task and return its stored record.

        ``run_at`` is set to ``now() + delay_seconds`` and ``deadline_at`` to
        ``now() + deadline_seconds``, both computed DB-side. ``payload`` is
        JSON-serialized on write. Duplicate enqueues create distinct rows —
        idempotency is a claim-time guarantee, not an insert-time one.
        """
        ...

    # ── claim (the single-winner CAS) ───────────────────────────────────
    def claim_batch(
        self,
        *,
        worker_id: str,
        env: str,
        limit: int,
        lease_seconds: int,
    ) -> List[TaskRecord]:
        """Atomically claim up to ``limit`` due tasks for ``worker_id``.

        Eligible = ``env`` matches, ``run_at <= now()``, and the row is either
        ``PENDING`` or a ``RUNNING`` row whose ``lease_expires_at <= now()``.
        A claimed row is flipped to ``RUNNING`` with ``claimed_by = worker_id``,
        ``lease_expires_at = now() + lease_seconds`` and ``attempts += 1``.

        An eligible candidate whose ``deadline_at <= now()`` is marked
        ``TIMED_OUT`` (terminal) instead of claimed, and is **not** returned —
        so a caller only ever receives tasks still within their deadline.

        Returns only the rows this worker actually won. Racing workers that
        targeted the same rows get fewer results, never duplicates.
        """
        ...

    # ── outcome transitions (CAS-guarded on the holder) ─────────────────
    def complete(self, *, task_id: int, worker_id: str) -> bool:
        """Mark a held task ``SUCCEEDED``. Returns ``False`` if this worker no
        longer holds it (lost lease / already terminal)."""
        ...

    def reschedule(
        self,
        *,
        task_id: int,
        worker_id: str,
        delay_seconds: float,
        error: Optional[str] = None,
    ) -> bool:
        """Return a held task to ``PENDING`` with ``run_at = now() +
        delay_seconds``, clearing the claim. Records ``error`` in
        ``last_error`` when given (the Retry path; the Reschedule path passes
        ``None``).

        If ``now() + delay_seconds >= deadline_at`` the task is marked
        ``TIMED_OUT`` instead of rescheduled (the deadline check is DB-side, so
        a retry whose backoff would overshoot the deadline gives up promptly
        rather than lingering). Returns ``False`` if this worker no longer
        holds the task.
        """
        ...

    def fail(self, *, task_id: int, worker_id: str, error: str) -> bool:
        """Mark a held task terminally ``FAILED`` with ``error``. Returns
        ``False`` if this worker no longer holds it."""
        ...

    # ── diagnosis / tests ───────────────────────────────────────────────
    def get_by_id(self, task_id: int) -> Optional[TaskRecord]:
        """Return the task by id, or ``None``."""
        ...

    def list_by_status(self, *, status: TaskStatus, env: str) -> List[TaskRecord]:
        """Return all tasks in ``status`` for ``env`` (diagnosis / tests)."""
        ...
