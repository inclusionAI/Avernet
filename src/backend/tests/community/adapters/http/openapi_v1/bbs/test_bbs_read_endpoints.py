from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.bbs.router import (
    get_topic,
    list_posts,
    list_topics,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.forum.models import (
    ForumPostPage,
    ForumPostRecord,
    ForumTopicPage,
    ForumTopicRecord,
)


def _request(path: str) -> Request:
    request = Request(scope={"type": "http", "method": "GET", "path": path})
    request.state.trace_id = "trace-read"
    return request


def _topic(body: str = "description") -> ForumTopicRecord:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    return ForumTopicRecord(
        topic_id="topic_1",
        author_type="HUMAN",
        author_id="user-a",
        title="Topic title",
        body=body,
        status="OPEN",
        created_at=now,
        updated_at=now,
    )


def _post() -> ForumPostRecord:
    now = datetime(2026, 9, 20, 8, 1, tzinfo=timezone.utc)
    return ForumPostRecord(
        post_id="post_1",
        topic_id="topic_1",
        author_type="BOT",
        author_id="bot-a",
        body="Reply body",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_topics_returns_preview_without_full_body():
    topic = _topic("x" * 501)

    class Service:
        def list_topics(self, **kwargs):
            assert kwargs == {
                "keyword": "query",
                "status": "open",
                "topic_type": None,
                "page": 2,
                "page_size": 5,
            }
            return ForumTopicPage(total=6, items=(topic,))

    payload = await list_topics(
        request=_request("/openapi/v1/bbs/topics"),
        page_params=PageParams(page=2, page_size=5),
        keyword="query",
        status="open",
        topic_type=None,
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.total == 6
    assert len(payload.data.items[0].body_preview) == 500
    assert payload.data.items[0].body_truncated is True
    assert payload.data.items[0].topic_type == "DISCUSSION"
    assert "body" not in payload.data.items[0].model_dump()


@pytest.mark.asyncio
async def test_get_topic_returns_full_body_without_posts():
    class Service:
        def get_topic(self, **kwargs):
            assert kwargs == {"topic_id": "topic_1"}
            return _topic("full description")

    payload = await get_topic(
        topic_id="topic_1",
        request=_request("/openapi/v1/bbs/topics/topic_1"),
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.body == "full description"
    assert "posts" not in payload.data.model_dump()


@pytest.mark.asyncio
async def test_list_posts_returns_separate_page_contract():
    class Service:
        def list_posts(self, **kwargs):
            assert kwargs == {"topic_id": "topic_1", "page": 1, "page_size": 20}
            return ForumPostPage(total=1, items=(_post(),))

    payload = await list_posts(
        topic_id="topic_1",
        request=_request("/openapi/v1/bbs/topics/topic_1/posts"),
        page_params=PageParams(),
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.total == 1
    assert payload.data.items[0].model_dump()["post_id"] == "post_1"
