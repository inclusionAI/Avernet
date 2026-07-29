"""In-memory ``TaskRepo`` / ``TaskEventRepo`` (Phase 2 community binding).

Satisfies the :class:`TaskRepo` / :class:`TaskEventRepo` Protocols structurally
so the real :class:`TaskService` can run end-to-end in the community profile
before the ORM persistence layer (plan Phase 1: ``ac_task`` /
``ac_task_event`` / ``ac_task_execution_graph`` SQLite/ZDAS ORM repos) lands.
Deep-copies on save/load so callers cannot mutate stored state by holding prior
references (the repo invariant). The event log is the single writer of ``seq``.

Phase 1 will swap these bindings for ORM-backed impls; the Protocol seam means
TaskService is untouched.
"""
from __future__ import annotations

import copy
from typing import Optional

from agentclaw.community.core.task.domain.events import (
    IllegalEventError,
    TaskEvent,
    next_seq,
)
from agentclaw.community.core.task.domain.models import Task
from agentclaw.community.core.task.domain.repository import (
    TaskNotFoundError,
)


class InMemoryTaskRepo:
    """Snapshot store; deep-copies on save and load."""

    def __init__(self) -> None:
        self._store: dict[str, Task] = {}

    def save(self, task: Task) -> None:
        self._store[task.id] = copy.deepcopy(task)

    def get_by_id(self, task_id: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return copy.deepcopy(task)

    def list_by_user(self, user_id: str) -> list[Task]:
        return [copy.deepcopy(t) for t in self._store.values() if t.user_id == user_id]


class InMemoryTaskEventRepo:
    """Append-only event log; single writer of the monotonic ``seq``."""

    def __init__(self) -> None:
        self._log: dict[str, list[TaskEvent]] = {}

    def append(self, event: TaskEvent) -> TaskEvent:
        expected = next_seq(self.latest_seq(event.task_id))
        if event.seq != expected:
            raise IllegalEventError(
                f"seq out of order for task {event.task_id}: "
                f"expected {expected}, got {event.seq}"
            )
        self._log.setdefault(event.task_id, []).append(copy.deepcopy(event))
        return event

    def load_events(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        return [
            copy.deepcopy(e)
            for e in self._log.get(task_id, [])
            if e.seq > after_seq
        ]

    def latest_seq(self, task_id: str) -> Optional[int]:
        events = self._log.get(task_id)
        if not events:
            return None
        return events[-1].seq


__all__ = ["InMemoryTaskEventRepo", "InMemoryTaskRepo"]