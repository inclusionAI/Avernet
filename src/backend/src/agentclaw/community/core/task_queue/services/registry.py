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
        """Fold a ``task_type`` the way MySQL/OceanBase would compare it."""
        return task_type.casefold().rstrip()

    def register(self, handler: TaskHandler) -> None:
        """Register ``handler`` under its ``task_type``.

        Raises :class:`ValueError` on a duplicate ``task_type`` — two handlers
        for the same type is a wiring bug, not a silent last-wins.

        Also raises when a ``task_type`` differs from an already-registered one
        *only by case or trailing whitespace*. That is not style policing: the
        enqueue dedup index is ``UNIQUE (env, task_type, active_idempotency_key)``,
        so two task types the index cannot tell apart share one dedup slot, and
        a keyed enqueue for ``"job"`` would be handed the live ``"Job"`` task
        with ``created=False`` — silently dropping work meant for the other
        handler.

        **This check is a second line of defence, not the enforcement.**
        ``task_type`` pins ``utf8mb4_bin`` in the schema, which settles the case
        half in the database, where it holds across processes. This check exists
        for what the collation cannot cover:

        - ``utf8mb4_bin`` is **PAD SPACE**, so ``"job "`` and ``"job"`` are
          still one index entry; only this check separates them.
        - It fails at **startup**, with a message naming both spellings, rather
          than at the first keyed enqueue in production.

        What it cannot do is see outside its own process — a rolling deploy
        renaming a task type by case alone would have each version's registry
        holding only its own spelling. That case is covered by the collation,
        which is precisely why the scope is enforced in the schema too rather
        than here alone.

        Lookup is unaffected: it stays exact, matching the collation.

        Lookup stays **exact**: rows store ``task_type`` verbatim, so the worker
        dispatches on the spelling that was enqueued.
        """
        task_type = handler.task_type
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
                f"{collides_with!r} only by case or trailing whitespace; the two "
                "are distinct here but equal in the enqueue dedup index under "
                "MySQL/OceanBase collation, so one handler's keyed enqueues "
                "would silently join the other's tasks — pick a distinct name"
            )
        self._handlers[task_type] = handler
        self._normalized[normalized] = task_type

    def get(self, task_type: str) -> Optional[TaskHandler]:
        """Return the handler for ``task_type``, or ``None`` if unregistered."""
        return self._handlers.get(task_type)
