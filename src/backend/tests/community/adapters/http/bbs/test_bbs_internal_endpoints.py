from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.bbs.router import (
    create_reply_internal,
    create_topic_internal,
    get_topic_internal,
    list_posts_internal,
    list_topics_internal,
    read_router,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    CreateReplyRequest,
    CreateTopicRequest,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.forum.models import (
    ForumPostPage,
    ForumPostRecord,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicPage,
    ForumTopicRecord,
)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-internal-bbs"
    return request


def _topic() -> ForumTopicRecord:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    return ForumTopicRecord(
        topic_id="topic_1",
        author_type="BOT",
        author_id="bot-a",
        title="Topic title",
        body="Topic description",
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


def test_internal_router_mirrors_all_five_bbs_operations():
    operations = {
        (method, route.path)
        for mounted in (read_router, router)
        for route in mounted.routes
        for method in route.methods
    }

    assert operations == {
        # Content surface (Topic + Post).
        ("GET", "/api/v1/bbs/topics"),
        ("GET", "/api/v1/bbs/topics/{topic_id}"),
        ("GET", "/api/v1/bbs/topics/{topic_id}/posts"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/topics"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/topics/{topic_id}/replies"),
        # BBS Browse Loop — subscription (per Bot) + actor-aware feed.
        ("GET", "/api/v1/bots/{bot_id}/bbs/browse-subscription"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/browse-subscription"),
        ("DELETE", "/api/v1/bots/{bot_id}/bbs/browse-subscription"),
        ("GET", "/api/v1/bots/{bot_id}/bbs/feed"),
        ("GET", "/api/v1/bbs/browse-loop/subscriptions"),
        ("GET", "/api/v1/bbs/browse-loop/subscriptions/{bot_id}"),
        # BBS Browse Loop — manual triggers (A=framework / B=openclaw).
        ("POST", "/api/v1/bbs/browse-loop/trigger-framework"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/browse-loop/trigger-self"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/browse-loop/cron-register"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/browse-loop/cron-remove"),
    }


@pytest.mark.asyncio
async def test_internal_reads_delegate_to_shared_forum_service():
    class Service:
        def list_topics(self, **kwargs):
            assert kwargs == {
                "keyword": "notice",
                "status": "open",
                "topic_type": None,
                "page": 2,
                "page_size": 10,
            }
            return ForumTopicPage(total=1, items=(_topic(),))

        def get_topic(self, **kwargs):
            assert kwargs == {"topic_id": "topic_1"}
            return _topic()

        def list_posts(self, **kwargs):
            assert kwargs == {"topic_id": "topic_1", "page": 1, "page_size": 20}
            return ForumPostPage(total=1, items=(_post(),))

    service = Service()
    topics = await list_topics_internal(
        request=_request("GET", "/api/v1/bbs/topics"),
        page_params=PageParams(page=2, page_size=10),
        keyword="notice",
        status="open",
        topic_type=None,
        service=service,
    )
    topic = await get_topic_internal(
        topic_id="topic_1",
        request=_request("GET", "/api/v1/bbs/topics/topic_1"),
        service=service,
    )
    posts = await list_posts_internal(
        topic_id="topic_1",
        request=_request("GET", "/api/v1/bbs/topics/topic_1/posts"),
        page_params=PageParams(),
        service=service,
    )

    assert topics.data is not None and topics.data.total == 1
    assert topic.data is not None and topic.data.topic_id == "topic_1"
    assert posts.data is not None and posts.data.items[0].post_id == "post_1"


@pytest.mark.asyncio
async def test_internal_topic_write_uses_path_bot_as_author():
    class Service:
        def create_topic(self, **kwargs):
            assert kwargs == {
                "author_type": "BOT",
                "author_id": "bot-a",
                "client_request_id": "request-1",
                "title": "Topic title",
                "body": "Topic description",
                "topic_type": "DISCUSSION",
            }
            return ForumTopicCreateResult(topic=_topic(), created=True)

    response = Response()
    payload = await create_topic_internal(
        body=CreateTopicRequest(
            client_request_id="request-1",
            title="Topic title",
            body="Topic description",
        ),
        bot_id="bot-a",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/topics"),
        response=response,
        service=Service(),
    )

    assert response.status_code == 201
    assert payload.data is not None and payload.data.topic_id == "topic_1"


@pytest.mark.asyncio
async def test_internal_reply_write_preserves_idempotent_replay_status():
    class Service:
        def create_reply(self, **kwargs):
            assert kwargs == {
                "topic_id": "topic_1",
                "author_type": "BOT",
                "author_id": "bot-a",
                "client_request_id": "request-2",
                "body": "Reply body",
            }
            return ForumReplyCreateResult(post=_post(), created=False)

    response = Response()
    payload = await create_reply_internal(
        body=CreateReplyRequest(
            client_request_id="request-2",
            body="Reply body",
        ),
        bot_id="bot-a",
        topic_id="topic_1",
        request=_request("POST", "/api/v1/bots/bot-a/bbs/topics/topic_1/replies"),
        response=response,
        service=Service(),
    )

    assert response.status_code == 200
    assert payload.data is not None and payload.data.post_id == "post_1"
