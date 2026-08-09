"""Handler registry — maps a ``task_type`` to the code that runs it.

A handler is anything satisfying :class:`TaskHandler`: a ``task_type`` it
serves plus a ``handle(payload)`` that returns a :class:`TaskOutcome`. The
worker looks up the handler for each claimed task's ``task_type`` and runs it.

**Registration contract (relies on the two-phase lifecycle).** Adopters
register their handlers in their own ``Lifecycle.bootstrap()``; the
``TaskWorker`` starts its claim loop in ``Lifecycle.startup()``. The app's
lifespan runner completes *every* ``bootstrap()`` before *any* ``startup()``
(``adapters/http/app.py`` + ``kernel/lifecycle.py``), so the registry is
guaranteed fully populated before the worker claims its first task — no
separate ordering mechanism is needed. The registry itself is a DI singleton,
so every participant sees the same instance.
"""
from __future__ import annotations

from typing import Dict, Optional, Protocol, runtime_checkable

from agentclaw.community.core.task_queue.types import TaskOutcome


@runtime_checkable
class TaskHandler(Protocol):
    """Runs one ``task_type``. Implementations are plain objects (no base
    class required) — registered into a :class:`HandlerRegistry`."""

    @property
    def task_type(self) -> str:
        """The task type this handler serves (the registry key)."""
        ...

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        """Execute the work for one task and return what to do next.

        Called by the worker off the event loop (via ``asyncio.to_thread``),
        so a synchronous body is fine. Returning is the normal path; raising
        is treated by the worker as an implicit :class:`Retry` (with backoff).
        Side effects must be safe to re-run — the queue guarantees
        single-claim execution but at-least-once invocation across crashes.
        """
        ...


class HandlerRegistry:
    """In-memory ``task_type`` → :class:`TaskHandler` map (a DI singleton)."""

    def __init__(self) -> None:
        self._handlers: Dict[str, TaskHandler] = {}
        #: Normalized ``task_type`` → the exact spelling registered under it.
        #: Only used to reject near-collisions; lookup stays exact.
        self._normalized: Dict[str, str] = {}

    @staticmethod
    def _normalize(task_type: str) -> str:
        """Fold a ``task_type`` the way MySQL/OceanBase would compare it.

        Only case folding: padding is handled by rejecting surrounding
        whitespace outright, so no registered type can carry any.
        """
        return task_type.casefold()

    def register(self, handler: TaskHandler) -> None:
        """Register ``handler`` under its ``task_type``.

        Raises :class:`ValueError` on a duplicate ``task_type`` — two handlers
        for the same type is a wiring bug, not a silent last-wins.

        Two further rules exist because ``task_type`` is a scope column of
        ``UNIQUE (env, task_type, active_idempotency_key)``: two task types the
        index cannot tell apart share one dedup slot, so a keyed enqueue for one
        is handed the *other's* live task with ``created=False``, silently
        dropping work. The two rules differ in kind, and the difference matters:

        **1. No leading or trailing whitespace — an absolute rule.** ``"job "``
        and ``"job"`` are one entry in the index because ``utf8mb4_bin`` is a
        PAD SPACE collation, and pinning the collation does not fix this. It
        must be an absolute property of every accepted type rather than a
        comparison against what is already registered, because a *pairwise*
        check is blind across processes: a rolling deploy renaming ``job`` to
        ``job `` has each version's registry holding only its own spelling, so
        neither sees a collision while the index merges them. Forbidding the
        padding outright holds regardless of what any other process registered,
        which a pairwise check cannot.

        **2. No two registered types differing only by case — a pairwise
        check, and a second line of defence.** The schema already settles this:
        ``task_type`` pins ``utf8mb4_bin``, which holds across processes. This
        check adds a loud failure at **startup**, naming both spellings, rather
        than a silent wrong-task join at the first keyed enqueue in production.
        Being pairwise, it has the cross-process blind spot described above —
        which is exactly why the collation, not this check, is the enforcement.

        Lookup stays **exact**: rows store ``task_type`` verbatim, so the worker
        dispatches on the spelling that was enqueued.
        """
        task_type = handler.task_type
        if task_type != task_type.strip():
            raise ValueError(
                f"task_type {task_type!r} must not have leading or trailing "
                "whitespace; MySQL/OceanBase compare with a PAD SPACE collation, "
                "so 'job ' and 'job' are one entry in the enqueue dedup index and "
                "one handler's keyed enqueues would silently join the other's "
                "tasks — strip it"
            )
        if task_type in self._handlers:
            raise ValueError(
                f"task_type {task_type!r} already has a handler registered "
                f"({type(self._handlers[task_type]).__name__})"
            )
        normalized = self._normalize(task_type)
        collides_with = self._normalized.get(normalized)
        if collides_with is not None:
            raise ValueError(
                f"task_type {task_type!r} differs from the already-registered "
                f"{collides_with!r} only by case; the two are distinct here but "
                "equal in the enqueue dedup index under MySQL/OceanBase "
                "collation, so one handler's keyed enqueues would silently join "
                "the other's tasks — pick a distinct name"
            )
        self._handlers[task_type] = handler
        self._normalized[normalized] = task_type

    def get(self, task_type: str) -> Optional[TaskHandler]:
        """Return the handler for ``task_type``, or ``None`` if unregistered."""
        return self._handlers.get(task_type)
