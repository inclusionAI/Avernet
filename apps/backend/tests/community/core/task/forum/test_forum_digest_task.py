from __future__ import annotations

from dataclasses import asdict

import pytest

from agentclaw.community.core.task.forum.digest.service import BbsDigestTaskService
from agentclaw.community.core.task.forum.models import (
    ForumPollStatisticsResult,
    ForumTopicTagStatisticsResult,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_DIGEST_TASK_NAME,
    BbsSandboxTask,
)


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def summarize(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return "probe healthy"

    async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return "[]"

class _FakeTopicTags:
    def __init__(self) -> None:
        self.calls: list[None] = []

    async def execute(self) -> ForumTopicTagStatisticsResult:
        self.calls.append(None)
        return ForumTopicTagStatisticsResult(
            topics_examined=4, topics_tagged=2
        )


class _FakePollStatistics:
    def __init__(self) -> None:
        self.calls: list[None] = []

    def execute(self) -> ForumPollStatisticsResult:
        self.calls.append(None)
        return ForumPollStatisticsResult(
            topics_processed=2, posts_processed=3, topics_closed=1
        )


@pytest.mark.asyncio
async def test_digest_task_runs_exact_poll_projection_before_health_probe():
    llm = _FakeLLM()
    poll_statistics = _FakePollStatistics()
    topic_tags = _FakeTopicTags()
    service = BbsDigestTaskService(
        llm=llm, poll_statistics=poll_statistics, topic_tags=topic_tags
    )

    result = await service.execute_once(BbsSandboxTask(name=BBS_DIGEST_TASK_NAME))

    assert poll_statistics.calls == [None]
    assert len(llm.calls) == 1
    assert topic_tags.calls == [None]
    assert result["poll_statistics"] == asdict(
        ForumPollStatisticsResult(
            topics_processed=2, posts_processed=3, topics_closed=1
        )
    )
    assert result["topic_tags"] == asdict(
        ForumTopicTagStatisticsResult(topics_examined=4, topics_tagged=2)
    )
    assert result["success"] is True
