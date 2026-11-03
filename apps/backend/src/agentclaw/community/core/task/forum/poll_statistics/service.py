"""Executable deterministic poll projection for the BBS poll-statistics task."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from injector import inject

from agentclaw.community.core.task.forum.poll_statistics_protocol import (
    ForumPollStatisticsServiceProtocol,
)
from agentclaw.community.core.task.forum.poll_statistics.protocols import (
    PollStatisticsTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_POLL_STATISTICS_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BbsPollStatisticsTaskService(PollStatisticsTaskExecutorProtocol):
    """Named wrapper around the exact POLL Topic projection service."""

    @inject
    def __init__(
        self,
        poll_statistics: ForumPollStatisticsServiceProtocol,
    ) -> None:
        self._poll_statistics = poll_statistics

    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        if task.name != BBS_POLL_STATISTICS_TASK_NAME:
            raise ValueError(f"unknown BBS poll-statistics task: {task.name!r}")

        result = self._poll_statistics.execute()
        logger.info("[bbs-poll-statistics][task] completed task=%s", task.name)
        return {
            "task_name": task.name,
            "success": True,
            "poll_statistics": asdict(result),
        }


__all__ = ["BbsPollStatisticsTaskService"]
