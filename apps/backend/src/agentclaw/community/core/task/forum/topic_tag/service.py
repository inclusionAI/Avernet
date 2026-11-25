"""Executable automatic tagging task for BBS Topics."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from injector import inject

from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_TOPIC_TAG_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.core.task.forum.topic_tag.protocols import (
    TopicTagTaskExecutorProtocol,
)
from agentclaw.community.core.task.forum.topic_tag_service_protocol import (
    ForumTopicTagServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class BbsTopicTagTaskService(TopicTagTaskExecutorProtocol):
    """Named wrapper around bounded Topic-tag classification."""

    @inject
    def __init__(self, topic_tags: ForumTopicTagServiceProtocol) -> None:
        self._topic_tags = topic_tags

    async def execute_once(self, task: BbsSandboxTask) -> dict[str, Any]:
        if task.name != BBS_TOPIC_TAG_TASK_NAME:
            raise ValueError(f"unknown BBS Topic-tag task: {task.name!r}")

        logger.info("[bbs-topic-tag][task] starting task=%s", task.name)
        started_at = time.perf_counter()
        try:
            result = await self._topic_tags.execute()
        except Exception:
            logger.exception("[bbs-topic-tag][task] failed task=%s", task.name)
            raise
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "[bbs-topic-tag][task] completed task=%s topics_examined=%s "
            "topics_tagged=%s duration_ms=%s",
            task.name,
            result.topics_examined,
            result.topics_tagged,
            duration_ms,
        )
        return {
            "task_name": task.name,
            "success": True,
            "topic_tags": asdict(result),
        }


__all__ = ["BbsTopicTagTaskService"]
