"""Executable BBS content-import task used to validate its scheduler.

This service is a separate backend-owned task from the digest task so content
ingestion can be enabled, scheduled, failed, and extended independently. It
also intentionally does not create topics or publish Bot-authored posts; later
phases can replace this slice with SourceItem parsing and draft Topic assembly
behind the same scheduler boundary.
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.task.forum.content_import.protocols import (
    ContentImportTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.shared.protocols import BbsLLMProtocol
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_CONTENT_IMPORT_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BbsContentImportTaskService(ContentImportTaskExecutorProtocol):
    """Named content-import execution service behind its distributed scheduler."""

    _LLM_MAX_TOKENS = 1024

    @inject
    def __init__(self, llm: BbsLLMProtocol) -> None:
        self._llm = llm

    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        if task.name != BBS_CONTENT_IMPORT_TASK_NAME:
            raise ValueError(f"unknown BBS content import task: {task.name!r}")

        prompt = (
            "In one concise sentence, state that the BBS content-import "
            "scheduling probe is healthy."
        )
        response = await self._llm.summarize(prompt, max_tokens=self._LLM_MAX_TOKENS)
        result: dict[str, Any] = {
            "task_name": task.name,
            "success": True,
            "summary": response,
        }
        logger.info("[bbs-content-import][task] completed task=%s", task.name)
        return result


__all__ = ["BbsContentImportTaskService"]
