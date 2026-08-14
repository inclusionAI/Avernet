"""Repository contracts owned by the ``platform`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, List, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.quality.models import QualityTaskRecord
    from agentclaw.community.core.session_resources.types import SessionResourceRecord
    from agentclaw.community.core.task_queue.types import EnqueueResult, TaskRecord, TaskStatus


@runtime_checkable
class TaskQueueRepositoryProtocol(Protocol):
    """Durable store for queued tasks with DB-level single-claimer semantics."""

    # ── enqueue ─────────────────────────────────────────────────────────
    @abstractmethod
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

        When a key is supplied, ``task_type`` must satisfy the same two rules
        (``ValueError`` for either) — it is the other scope column of the dedup
        index, so it decides which key space a row lands in:

        - **no leading or trailing whitespace**, since a padded value shares a
          dedup slot with the unpadded one under PAD SPACE and could suppress a
          legitimate enqueue for it;
        - **at most 100 characters**, the stored column width, since a
          truncating server files the row under the *truncated* scope while the
          holder lookup searches for the full string — the duplicate then
          conflicts with a row it cannot find and raises instead of joining it.

        Un-keyed enqueues are unaffected by both: their
        ``active_idempotency_key`` is ``NULL``, so they never enter the index and
        any ``task_type`` remains acceptable.

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
    @abstractmethod
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
    @abstractmethod
    def complete(self, *, task_id: int, worker_id: str) -> bool:
        """Mark a held task ``SUCCEEDED``. Returns ``False`` if this worker no
        longer holds it (lost lease / already terminal)."""
        ...

    @abstractmethod
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

    @abstractmethod
    def fail(self, *, task_id: int, worker_id: str, error: str) -> bool:
        """Mark a held task terminally ``FAILED`` with ``error``. Returns
        ``False`` if this worker no longer holds it."""
        ...

    @abstractmethod
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
    @abstractmethod
    def get_by_id(self, task_id: int) -> Optional[TaskRecord]:
        """Return the task by id, or ``None``."""
        ...

    @abstractmethod
    def list_by_status(self, *, status: TaskStatus, env: str) -> List[TaskRecord]:
        """Return all tasks in ``status`` for ``env`` (diagnosis / tests)."""
        ...


@runtime_checkable
class QualityTaskRepository(Protocol):
    """Protocol for quality task repository implementations.

    Implementation: a single unified ORM body at
    ``plugins.quality_repository.QualityTaskRepository`` (runs on both
    the corp store and SQLite via the injected ``DatabasePlugin``).
    """

    @abstractmethod
    def list_by_conditions(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QualityTaskRecord], int]:
        """List quality tasks by conditions with pagination.

        Args:
            task_type: Task type filter (required, e.g., "eval")
            biz_type: Business type filter (required, e.g., "service_bot_single")
            bot_id: Optional bot ID filter
            owner_id: Optional owner ID filter
            page: Page number (1-indexed)
            page_size: Page size

        Returns:
            Tuple of (list of records, total count)
        """
        ...

    @abstractmethod
    def get_by_uuid(self, uuid: str) -> QualityTaskRecord | None:
        """Get a quality task by UUID.

        Args:
            uuid: Task UUID

        Returns:
            QualityTaskRecord if found, None otherwise
        """
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> QualityTaskRecord | None:
        """Get a quality task by ID.

        Args:
            id: Task ID (primary key)

        Returns:
            QualityTaskRecord if found, None otherwise
        """
        ...

    @abstractmethod
    def create(
        self,
        *,
        uuid: str | None = None,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        ext: dict[str, Any] | None = None,
        operator_id: str | None = None,
    ) -> QualityTaskRecord:
        """Create a new quality task.

        Args:
            uuid: Optional task UUID
            task_type: Task type
            biz_type: Business type
            bot_id: Optional bot ID
            owner_id: Optional owner ID
            ext: Optional extension data (JSON)
            operator_id: Optional operator ID

        Returns:
            Created QualityTaskRecord
        """
        ...

    @abstractmethod
    def update_status(
        self, id: int, status: str, ext: dict[str, Any] | None = None
    ) -> QualityTaskRecord | None:
        """Update the status of a quality task.

        Args:
            id: Task ID (primary key)
            status: New status
            ext: Optional extension data to merge/update

        Returns:
            Updated QualityTaskRecord if found, None otherwise
        """
        ...

    @abstractmethod
    def update_ext(self, id: int, ext: dict[str, Any]) -> QualityTaskRecord | None:
        """Update only the ext field of a quality task.

        Args:
            id: Task ID (primary key)
            ext: Extension data to merge/update

        Returns:
            Updated QualityTaskRecord if found, None otherwise
        """
        ...


class ResourceRepositoryProtocol(Protocol):
    """Persistent storage for resources (files, URLs, nodes)."""

    @abstractmethod
    def get_by_id(self, resource_id: str) -> Optional[dict]:
        """Fetch a single resource by primary key, or None."""
        ...

    @abstractmethod
    def get_by_path(self, path: str, bolt_id: Optional[str] = None) -> Optional[dict]:
        """Fetch a FILE resource by its stored filesystem path."""
        ...

    @abstractmethod
    def list_resources(
        self,
        resource_type: Optional[str] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        bolt_id: Optional[str] = None,
    ) -> List[dict]:
        """List resources matching the given filters."""
        ...

    @abstractmethod
    def create(self, resource_data: dict) -> dict:
        """Insert a new resource and return the stored representation."""
        ...

    @abstractmethod
    def update(self, resource_id: str, resource_data: dict) -> Optional[dict]:
        """Update fields on an existing resource, or None if missing."""
        ...

    @abstractmethod
    def delete(self, resource_id: str) -> bool:
        """Soft-delete (status=deleted). Returns True if a row was updated."""
        ...

    @abstractmethod
    def hard_delete(self, resource_id: str) -> bool:
        """Physically remove the row."""
        ...

    @abstractmethod
    def count_resources(
        self,
        resource_type: Optional[str] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        bolt_id: Optional[str] = None,
    ) -> int:
        """Count resources matching the given filters."""
        ...


@runtime_checkable
class SessionResourceRepositoryProtocol(Protocol):
    @abstractmethod
    def create(self, record: SessionResourceRecord) -> SessionResourceRecord: ...

    @abstractmethod
    def get_by_resource_id(self, resource_id: str) -> SessionResourceRecord | None: ...

    @abstractmethod
    def get_owned(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord | None: ...
    @abstractmethod
    def list_owned(
        self,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> list[SessionResourceRecord]: ...

    @abstractmethod
    def cas_start_materialization(self, **kwargs) -> SessionResourceRecord | None: ...

    @abstractmethod
    def cas_finish_materialization(self, **kwargs) -> SessionResourceRecord | None: ...

    @abstractmethod
    def soft_delete(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord | None: ...
