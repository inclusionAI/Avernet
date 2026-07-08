"""Enqueue facade — the programmatic entry point adopters call.

Thin wrapper over the repository. Timing is owned by the database: this service
just forwards the relative ``delay_seconds`` / ``deadline_seconds`` durations
(the repository turns them into absolute ``run_at`` / ``deadline_at`` with the
DB clock) and stamps the current ``env``.
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.task_queue.repository.protocol import TaskQueueRepositoryProtocol
from agentclaw.community.core.task_queue.types import TaskRecord
from agentclaw.community.utils.env_utils import get_current_env


class TaskQueueService:
    """Persist background work for the in-process worker to pick up."""

    @inject
    def __init__(self, repo: TaskQueueRepositoryProtocol) -> None:
        self._repo = repo

    def enqueue(
        self,
        task_type: str,
        payload: dict,
        deadline_seconds: int,
        *,
        delay_seconds: int = 0,
    ) -> TaskRecord:
        """Enqueue a task.

        - ``task_type`` — the registry key whose handler will run it.
        - ``payload`` — the (required) work description; persisted as JSON.
        - ``deadline_seconds`` — give-up horizon from now; every task must have
          one. Past it, the task is retired ``TIMED_OUT`` (enforced DB-side).
        - ``delay_seconds`` — how long until the task first becomes eligible
          (``run_at = now + delay``); ``0`` (default) means immediately.
        """
        return self._repo.enqueue(
            task_type=task_type,
            payload=payload,
            delay_seconds=delay_seconds,
            deadline_seconds=deadline_seconds,
            env=get_current_env(),
        )
