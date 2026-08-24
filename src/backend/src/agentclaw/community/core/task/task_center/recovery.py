"""Recover persisted non-terminal task graphs after an instance restart."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agentclaw.community.core.repository.protocols.task import TaskGraphRepositoryProtocol

logger = logging.getLogger("task.recovery")


class TaskRecoveryWorker:
    """One-shot recovery worker; the application scheduler owns its cadence."""

    def __init__(
        self,
        graph_repo: TaskGraphRepositoryProtocol,
        resume: Callable[[str], Awaitable[None]],
        *,
        instance_id: str,
        lease_seconds: int = 60,
    ) -> None:
        self._repo = graph_repo
        self._resume = resume
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds

    async def recover_once(self, *, limit: int = 100) -> list[str]:
        recovered: list[str] = []
        for task_id in self._repo.list_recoverable(limit=limit):
            if not self._repo.acquire_lease(
                task_id,
                instance_id=self._instance_id,
                lease_seconds=self._lease_seconds,
            ):
                continue
            try:
                if self._repo.load_graph(task_id) is None:
                    continue
                await self._resume(task_id)
                recovered.append(task_id)
            except Exception:
                logger.exception("[task]task recovery failed task_id=%s", task_id)
            finally:
                self._repo.release_lease(task_id, instance_id=self._instance_id)
        return recovered
