from __future__ import annotations

import logging
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
    TOPIC_TAG_VOCABULARY,
    ForumTopicTagService,
)

_NOW = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)


def test_topic_tag_vocabulary_is_closed_and_bounded():
    assert TOPIC_TAG_VOCABULARY == (
        "产品", "设计", "工程", "架构", "前端", "后端",
        "AI", "数据", "安全", "性能", "质量", "测试",
        "运维", "效率", "需求", "方案", "评审", "协作",
        "管理", "公告", "投票", "反馈", "提问", "分享",
        "风险", "成长", "趣味", "人设", "偏好", "观察",
    )
    assert len(TOPIC_TAG_VOCABULARY) == len(set(TOPIC_TAG_VOCABULARY)) == 30


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
        self.budgets: list[int] = []

    async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
        self.prompts.append(prompt)
        self.budgets.append(max_tokens)
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
    assert llm.budgets == [ForumTopicTagService._LLM_MAX_TOKENS]
    assert tag_repository.updates == [
        {"topic_id": "topic-untagged", "tags": ("工程", "架构")}
    ]


@pytest.mark.asyncio
async def test_topic_tag_service_identifies_disabled_llm_reason(caplog):
    caplog.set_level(logging.INFO, logger="start")

    class DisabledLLM(FakeLLM):
        async def generate_tags(self, prompt: str, *, max_tokens: int) -> str:
            return "[bbs llm disabled]"

    service = ForumTopicTagService(
        repository=FakeReadRepository(),
        tag_repository=FakeTagRepository(),
        llm=DisabledLLM(),
    )

    result = await service.execute()

    assert result == ForumTopicTagStatisticsResult(
        topics_examined=1, topics_tagged=0
    )
    assert (
        "[bbs-topic-tag][service] skip topic=topic-untagged reason=llm_disabled"
        in {record.getMessage() for record in caplog.records}
    )


@pytest.mark.asyncio
async def test_topic_tag_service_skips_write_when_llm_returns_no_valid_tags(caplog):
    caplog.set_level(logging.INFO, logger="start")
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
    messages = {record.getMessage() for record in caplog.records}
    assert (
        "[bbs-topic-tag][service] scan page_items=2 page_total=2 "
        "already_tagged=1 untagged=1 selected=1" in messages
    )
    assert (
        "[bbs-topic-tag][service] skip topic=topic-untagged "
        "reason=invalid_model_output" in messages
    )
    assert any(
        message.startswith(
            "[bbs-topic-tag][service] completed topics_examined=1 "
            "topics_tagged=0 duration_ms="
        )
        for message in messages
    )
