"""Enqueue facade — the programmatic entry point adopters call.

Thin wrapper over the repository. Timing is owned by the database: this service
just forwards the relative ``delay_seconds`` / ``deadline_seconds`` durations
(the repository turns them into absolute ``run_at`` / ``deadline_at`` with the
DB clock) and stamps the current ``env``.
"""
from __future__ import annotations

from injector import inject

from typing import Optional

from agentclaw.community.core.task_queue.repository.protocol import TaskQueueRepositoryProtocol
from agentclaw.community.core.task_queue.types import EnqueueResult
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
        idempotency_key: Optional[str] = None,
    ) -> EnqueueResult:
        """Enqueue a task. Returns ``(record, created)``.

        - ``task_type`` — the registry key whose handler will run it.
        - ``payload`` — the (required) work description; persisted as JSON.
        - ``deadline_seconds`` — give-up horizon from now; every task must have
          one. Past it, the task is retired ``TIMED_OUT`` (enforced DB-side).
        - ``delay_seconds`` — how long until the task first becomes eligible
          (``run_at = now + delay``); ``0`` (default) means immediately.
        - ``idempotency_key`` — opt-in submission dedup. With a key, at most one
          **live** task exists per key within this ``(env, task_type)``: a
          duplicate enqueue inserts nothing and returns the live task with
          ``created=False``. Terminal tasks release their key, so a retry or a
          later re-run of the same logical work is *not* suppressed. Omit it
          (the default) for work that should always produce a distinct row —
          recurring polls, timers, genuine fan-out. Must be non-empty, at most
          190 characters (the stored column width), and free of leading or
          trailing whitespace; all three raise ``ValueError`` rather than
          risking a silent collision of two distinct keys on MySQL/OceanBase
          (truncation under a non-strict server, space padding under the
          collation).

        See ``TaskQueueRepositoryProtocol.enqueue`` for the key convention and
        the full contract.
        """
        return self._repo.enqueue(
            task_type=task_type,
            payload=payload,
            delay_seconds=delay_seconds,
            deadline_seconds=deadline_seconds,
            env=get_current_env(),
            idempotency_key=idempotency_key,
        )
