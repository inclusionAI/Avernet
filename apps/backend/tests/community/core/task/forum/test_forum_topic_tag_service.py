from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.task.forum.models import (
    ForumPostPage,
    ForumPostRecord,
    ForumTopicAnalysisRecord,
    ForumTopicPage,
    ForumTopicRecord,
    ForumTopicTagStatisticsResult,
)
from agentclaw.community.core.task.forum.services.topic_tag_service import (
    ForumTopicTagService,
)

_NOW = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)


def _topic(topic_id: str, *, tags: tuple[str, ...] = ()) -> ForumTopicRecord:
    return ForumTopicRecord(
        topic_id=topic_id,
        author_type="HUMAN",
        author_id="user-a",
        title="Topic title",
        body="Topic body",
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(tags=tags),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _reply(reply_id: str, body: str) -> ForumPostRecord:
    return ForumPostRecord(
        post_id=reply_id,
        topic_id="topic-untagged",
        author_type="BOT",
        author_id="bot-a",
        body=body,
        created_at=_NOW,
        updated_at=_NOW,
    )


class FakeReadRepository:
    def __init__(self) -> None:
        self.list_posts_calls: list[dict[str, int | str]] = []

    def list_topics(self, **kwargs):
        return ForumTopicPage(
            total=2,
            items=(
                _topic("topic-tagged", tags=("公告",)),
                _topic("topic-untagged"),
            ),
        )

    def list_posts(self, *, topic_id, offset=0, limit=20):
        self.list_posts_calls.append(
            {"topic_id": topic_id, "offset": offset, "limit": limit}
        )
        return ForumPostPage(total=1, items=(_reply("post-1", "Reply context"),))


class FakeTagRepository:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update_topic_tags(self, *, topic_id, tags):
        self.updates.append({"topic_id": topic_id, "tags": tags})
        return _topic(topic_id, tags=tags)


class FakeLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
        self.prompts.append(prompt)
        return '```json\n["工程", "架构", "不在词表"]\n```'

    async def summarize(self, prompt: str, *, max_tokens: int) -> str:
        raise AssertionError("tagging must not call summarize")


@pytest.mark.asyncio
async def test_topic_tag_service_uses_context_and_fixed_vocabulary():
    repository = FakeReadRepository()
    tag_repository = FakeTagRepository()
    llm = FakeLLM()
    service = ForumTopicTagService(
        repository=repository, tag_repository=tag_repository, llm=llm
    )

    result = await service.execute()

    assert result == ForumTopicTagStatisticsResult(
        topics_examined=1, topics_tagged=1
    )
    assert len(llm.prompts) == 1
    assert "Topic title" in llm.prompts[0]
    assert "Reply context" in llm.prompts[0]
    assert tag_repository.updates == [
        {"topic_id": "topic-untagged", "tags": ("工程", "架构")}
    ]


@pytest.mark.asyncio
async def test_topic_tag_service_skips_write_when_llm_returns_no_valid_tags():
    class EmptyLLM(FakeLLM):
        async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
            return '["未知标签"]'

    tag_repository = FakeTagRepository()
    service = ForumTopicTagService(
        repository=FakeReadRepository(),
        tag_repository=tag_repository,
        llm=EmptyLLM(),
    )

    result = await service.execute()

    assert result == ForumTopicTagStatisticsResult(
        topics_examined=1, topics_tagged=0
    )
    assert tag_repository.updates == []
