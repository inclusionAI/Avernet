"""TaskRepo / TaskEventRepo protocols (Phase 0.4).

Persistence seam for the task aggregate. TaskService depends on these
Protocols; the plugin layer implements them — community: SQLite/ORM via
DatabasePlugin (one body, two backends: SQLite + the corp ORM backend),
bound in Phase 1.

Both are ``@runtime_checkable`` structural Protocols so a conforming in-memory
Fake (tests) or ORM-backed impl (plugins) satisfies them without inheritance.

Contract invariants:
- :class:`TaskEventRepo` is the **single writer** of the event log; ``append``
  assigns / validates the monotonic ``seq`` per task (no gaps, no reuse).
- :class:`TaskRepo` snapshots the aggregate (``spec`` + ``execution_graph``)
  by primary key; it must deep-copy on save/load so callers cannot mutate store
  state by holding prior references.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from .events import TaskEvent
from .models import Task


class TaskNotFoundError(ValueError):
    """Raised when a task id cannot be resolved by :meth:`TaskRepo.get_by_id`."""


class EventNotFoundError(ValueError):
    """Raised when an event cannot be located in the event log."""


@runtime_checkable
class TaskRepo(Protocol):
    """Snapshot repository for the :class:`Task` aggregate.

    Implementations MUST deep-copy the aggregate on save and on load so that
    in-memory references held by callers cannot mutate stored state. The event
    log (:class:`TaskEventRepo`) is the source of truth; this repo is a
    materialized fold for fast reads.
    """

    def save(self, task: Task) -> None:
        """Persist (upsert) the full aggregate snapshot by ``task.id``."""
        ...

    def get_by_id(self, task_id: str) -> Task:
        """Return the aggregate, raising :class:`TaskNotFoundError` if absent."""
        ...

    def list_by_user(self, user_id: str) -> list[Task]:
        """Return all tasks owned by ``user_id`` (snapshot order is unspecified)."""
        ...


@runtime_checkable
class TaskEventRepo(Protocol):
    """Append-only event log; the single writer of ``seq``.

    ``append`` is the only mutation path. It MUST validate that the supplied
    ``event.seq`` equals ``next_seq(latest)`` for ``event.task_id`` (rejecting
    gaps / reuse / out-of-order), then durably append and return the event.
    Concurrent appends serialize on the per-task watermark.
    """

    def append(self, event: TaskEvent) -> TaskEvent:
        """Validate ``seq`` against the per-task watermark and durably append."""
        ...

    def load_events(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        """Return events for ``task_id`` with ``seq > after_seq``, ascending by seq."""
        ...

    def latest_seq(self, task_id: str) -> Optional[int]:
        """Return the highest ``seq`` appended for ``task_id`` (None if no events)."""
        ...

    def truncate(self, task_id: str, after_seq: int) -> None:
        """Drop events with ``seq > after_seq`` for ``task_id`` (rollback / checkpoint
        only; the log is otherwise append-only). impls MUST keep ``seq ≤ after_seq``."""
        ...