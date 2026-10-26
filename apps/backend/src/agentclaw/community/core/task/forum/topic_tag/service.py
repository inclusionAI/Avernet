"""Executable automatic tagging task for BBS Topics."""

from __future__ import annotations

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

        result = await self._topic_tags.execute()
        logger.info("[bbs-topic-tag][task] completed task=%s", task.name)
        return {
            "task_name": task.name,
            "success": True,
            "topic_tags": asdict(result),
        }


__all__ = ["BbsTopicTagTaskService"]
