from __future__ import annotations

import pytest

from agentclaw.community.core.task.forum.models import ForumTopicTagStatisticsResult
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_TOPIC_TAG_TASK_NAME,
    BbsSandboxTask,
)
from agentclaw.community.core.task.forum.topic_tag.service import BbsTopicTagTaskService


class _FakeTopicTags:
    def __init__(self) -> None:
        self.calls: list[None] = []

    async def execute(self) -> ForumTopicTagStatisticsResult:
        self.calls.append(None)
        return ForumTopicTagStatisticsResult(
            topics_examined=4, topics_tagged=2
        )


@pytest.mark.asyncio
async def test_topic_tag_task_classifies_topics_through_tag_service():
    topic_tags = _FakeTopicTags()
    service = BbsTopicTagTaskService(topic_tags=topic_tags)

    result = await service.execute_once(BbsSandboxTask(name=BBS_TOPIC_TAG_TASK_NAME))

    assert topic_tags.calls == [None]
    assert result == {
        "task_name": BBS_TOPIC_TAG_TASK_NAME,
        "success": True,
        "topic_tags": {"topics_examined": 4, "topics_tagged": 2},
    }


@pytest.mark.asyncio
async def test_topic_tag_task_rejects_unknown_task_name():
    service = BbsTopicTagTaskService(topic_tags=_FakeTopicTags())

    with pytest.raises(ValueError, match="unknown BBS Topic-tag task"):
        await service.execute_once(BbsSandboxTask(name="digest_task"))
