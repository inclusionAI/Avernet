from __future__ import annotations

from dataclasses import asdict

import pytest

from agentclaw.community.core.task.forum.digest.service import BbsDigestTaskService
from agentclaw.community.core.task.forum.models import ForumPollStatisticsResult
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
    service = BbsDigestTaskService(llm=llm, poll_statistics=poll_statistics)

    result = await service.execute_once(BbsSandboxTask(name=BBS_DIGEST_TASK_NAME))

    assert poll_statistics.calls == [None]
    assert len(llm.calls) == 1
    assert result["poll_statistics"] == asdict(
        ForumPollStatisticsResult(
            topics_processed=2, posts_processed=3, topics_closed=1
        )
    )
    assert result["success"] is True
