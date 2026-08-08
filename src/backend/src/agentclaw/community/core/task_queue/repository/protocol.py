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

Idempotency is enforced at **two** points, answering two different questions.

**Claim time — "who runs it?"** A row-level compare-and-swap UPDATE whose
predicate only matches an unclaimed (or lease-expired) row, so across N racing
workers each task is won by at most one. Past-deadline candidates are marked
``TIMED_OUT`` instead of claimed. ``complete`` / ``reschedule`` / ``fail`` are
CAS-guarded on ``claimed_by == worker_id AND status == RUNNING`` so a worker
that lost its lease cannot clobber a task another worker took over.

**Enqueue time — "should this row exist at all?"** Opt-in, via
``idempotency_key``. See :meth:`TaskQueueRepositoryProtocol.enqueue`. A caller
that supplies no key gets exactly the old behavior: every enqueue is a new row.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from agentclaw.community.core.task_queue.types import (
    EnqueueResult,
    TaskRecord,
    TaskStatus,
)


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
        idempotency_key: Optional[str] = None,
    ) -> EnqueueResult:
        """Persist a ``PENDING`` task and return ``(record, created)``.

        ``run_at`` is set to ``now() + delay_seconds`` and ``deadline_at`` to
        ``now() + deadline_seconds``, both computed DB-side. ``payload`` is
        JSON-serialized on write.

        **Without a key** (the default) every enqueue creates a distinct row —
        insert-time dedup is opt-in, so un-keyed callers are unaffected by it.

        **With a key** dedup is *active-only*: at most one **live** task per
        ``idempotency_key`` within an ``(env, task_type)``. If a live task
        already holds the key, no row is inserted and that task is returned with
        ``created=False``; otherwise a new row is created with ``created=True``.
        Never raises for a plain duplicate.

        Nor for the race around one: if the holder goes terminal between the
        insert losing and the holder being looked up, the key is free again and
        the insert is simply retried. Losing that race repeatedly is bad luck
        rather than an error, and the retry budget is sized so it is not
        mistaken for one. Two cases *do* raise ``RuntimeError`` — both mean the
        enqueue could not be honoured, so neither can be reported as a
        duplicate: a key held by a **terminal** row that never released it
        (an inconsistent row; the message names it), and the key being taken and
        released by other callers on every attempt (sustained churn).

        A terminal transition (``SUCCEEDED`` / ``FAILED`` / ``TIMED_OUT``)
        **releases** the key, so the same key may legitimately be re-enqueued
        afterwards — which is what makes retry, re-poll, and repeated restart
        work. Scope your key to a generation only when you want the *opposite*.

        Key convention::

            <entity>:<entity_id>[:<qualifier>][:<generation>]
            publish:1234:online_release
            skills_pool:prod:e-9:bot-7

        A key must be non-empty and at most **190 characters** — the stored
        column width. Both are enforced in Python and raise ``ValueError``,
        because the engines disagree about overflow (SQLite ignores the bound,
        strict MySQL errors, non-strict MySQL *silently truncates* and would
        collide two distinct keys). Embed ids with care: some id columns are
        far wider than 190, so hash the variable part rather than letting a
        long id blow the bound.

        When a key is supplied, ``task_type`` must carry no leading or trailing
        whitespace either (``ValueError``) — it is the other scope column of the
        dedup index, so a padded value shares a dedup slot with the unpadded one
        under PAD SPACE and could suppress a legitimate enqueue for it. Un-keyed
        enqueues are unaffected: their ``active_idempotency_key`` is ``NULL``, so
        they never enter the index and any ``task_type`` remains acceptable.

        A key must also carry **no leading or trailing whitespace** (also
        ``ValueError``). Internal spacing is untouched and keys are stored
        verbatim; only the ends are constrained, because MySQL/OceanBase
        compare with a PAD SPACE collation under which ``"k1"`` and ``"k1 "``
        are the *same* index entry while SQLite keeps them apart. Rejecting
        such keys makes the collision unreachable rather than relying on a
        NO PAD collation being available.

        One edge worth knowing: a task whose deadline has passed but which no
        worker has scanned yet is still non-terminal, so it still holds its key
        and a duplicate enqueue joins it. The next claim scan retires it
        ``TIMED_OUT`` and frees the key. This only bites when the worker is down
        or behind by longer than the task's own deadline.
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

    def renew_lease(self, *, task_id: int, worker_id: str, lease_seconds: int) -> bool:
        """Extend a held task's lease to ``now() + lease_seconds`` (DB clock).

        CAS-guarded on ``claimed_by == worker_id AND status == RUNNING`` — the same
        holder predicate as the outcome transitions — so a worker that already lost
        its lease to another cannot extend it. Used by the worker's heartbeat to
        keep a long-running handler's claim alive past the base ``lease_seconds``.
        Returns ``False`` if this worker no longer holds the task (its lease
        expired and another worker took over, or it is already terminal)."""
        ...

    # ── diagnosis / tests ───────────────────────────────────────────────
    def get_by_id(self, task_id: int) -> Optional[TaskRecord]:
        """Return the task by id, or ``None``."""
        ...

    def list_by_status(self, *, status: TaskStatus, env: str) -> List[TaskRecord]:
        """Return all tasks in ``status`` for ``env`` (diagnosis / tests)."""
        ...
