"""Executable BBS digest task used to validate the background scheduler.

The first runtime slice intentionally executes one named task rather than
dispatching a Bot. Later phases can replace this service with the real
topic/Vote/Summary aggregation while preserving the scheduler, lease,
configuration, and lifecycle boundaries.
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.task.forum.digest.protocols import (
    DigestTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.shared.protocols import BbsLLMProtocol
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_DIGEST_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BbsDigestTaskService(DigestTaskExecutorProtocol):
    """Named digest execution service behind the distributed scheduler."""

    @inject
    def __init__(self, llm: BbsLLMProtocol) -> None:
        self._llm = llm

    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        if task.name != BBS_DIGEST_TASK_NAME:
            raise ValueError(f"unknown BBS digest task: {task.name!r}")

        prompt = (
            "In one concise sentence, state that the BBS digest "
            "scheduling probe is healthy."
        )
        response = await self._llm.summarize(prompt, max_tokens=128)
        result: dict[str, Any] = {
            "task_name": task.name,
            "success": True,
            "summary": response,
        }
        logger.info("[bbs-digest][task] completed task=%s", task.name)
        return result


__all__ = ["BbsDigestTaskService"]
