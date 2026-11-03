from __future__ import annotations

import pytest

from agentclaw.community.core.task.forum.models import ForumPollStatisticsResult
from agentclaw.community.core.task.forum.poll_statistics.service import (
    BbsPollStatisticsTaskService,
)
from agentclaw.community.core.task.forum.shared.task.sandbox import (
    BBS_POLL_STATISTICS_TASK_NAME,
    BbsSandboxTask,
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
async def test_poll_statistics_task_projects_votes_without_llm():
    poll_statistics = _FakePollStatistics()
    service = BbsPollStatisticsTaskService(poll_statistics=poll_statistics)

    result = await service.execute_once(
        BbsSandboxTask(name=BBS_POLL_STATISTICS_TASK_NAME)
    )

    assert poll_statistics.calls == [None]
    assert result == {
        "task_name": BBS_POLL_STATISTICS_TASK_NAME,
        "success": True,
        "poll_statistics": {
            "topics_processed": 2,
            "posts_processed": 3,
            "topics_closed": 1,
        },
    }


@pytest.mark.asyncio
async def test_poll_statistics_task_rejects_unknown_task_name():
    service = BbsPollStatisticsTaskService(poll_statistics=_FakePollStatistics())

    with pytest.raises(ValueError, match="unknown BBS poll-statistics task"):
        await service.execute_once(BbsSandboxTask(name="digest_task"))
