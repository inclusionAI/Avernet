from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.bbs.router import (
    close_topic_internal,
    create_reply_internal,
    create_topic_internal,
    get_topic_internal,
    list_posts_internal,
    list_topics_internal,
    read_router,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    CloseTopicRequestUnified,
    CreateReplyRequestUnified,
    CreateTopicRequestUnified,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.errors import Conflict, Forbidden, NotFound
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


def test_internal_router_mirrors_all_bbs_operations():
    operations = {
        (method, route.path)
        for mounted in (read_router, router)
        for route in mounted.routes
        for method in route.methods
    }

    assert operations == {
        # Content surface (Topic + Post) — reads.
        ("GET", "/api/v1/bbs/topics"),
        ("GET", "/api/v1/bbs/topics/{topic_id}"),
        ("GET", "/api/v1/bbs/topics/{topic_id}/posts"),
        # Content surface — unified writes (author declared in the body).
        ("POST", "/api/v1/bbs/topics"),
        ("POST", "/api/v1/bbs/topics/{topic_id}/posts"),
        ("POST", "/api/v1/bbs/topics/{topic_id}/close"),
        # BBS Browse Loop — subscription (per Bot) + actor-aware feed.
        ("GET", "/api/v1/bots/{bot_id}/bbs/browse-subscription"),
        ("POST", "/api/v1/bots/{bot_id}/bbs/browse-subscription"),
        ("DELETE", "/api/v1/bots/{bot_id}/bbs/browse-subscription"),
        ("GET", "/api/v1/bots/{bot_id}/bbs/feed"),
        ("GET", "/api/v1/bbs/browse-subscriptions"),
        # BBS Browse Loop — manual triggers (A=framework / B=openclaw).
        ("POST", "/api/v1/bots/{bot_id}/bbs/browse-loop/trigger-framework"),
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
                "author_id": None,
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
        author_id=None,
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


# ---------------------------------------------------------------------------
# BBS Unified Writes — internal /api/v1/bbs mirror tests
# ---------------------------------------------------------------------------
# Mirror of ``test_bbs_unified_write_endpoints.py`` (the public /openapi
# surface): the internal /api/v1 surface delegates to the same
# ``ForumServiceProtocol`` so the author-gating and idempotency semantics cannot
# drift. ``author_type`` + ``author_id`` are declared in the body; there is no
# ``bot_id`` path segment and no ``topic_type`` field.


@pytest.mark.asyncio
async def test_internal_topic_create_passes_body_author_as_bot_author():
    class Service:
        def create_topic(self, **kwargs):
            assert kwargs == {
                "author_type": "BOT",
                "author_id": "bot-a",
                "client_request_id": "request-1",
                "title": "Topic title",
                "body": "Topic description",
            }
            return ForumTopicCreateResult(topic=_topic(), created=True)

    response = Response()
    payload = await create_topic_internal(
        body=CreateTopicRequestUnified(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="request-1",
            title="Topic title",
            body="Topic description",
        ),
        request=_request("POST", "/api/v1/bbs/topics"),
        response=response,
        service=Service(),
    )

    assert response.status_code == 201
    assert payload.data is not None and payload.data.topic_id == "topic_1"


@pytest.mark.asyncio
async def test_internal_topic_create_passes_body_author_as_human_author():
    class Service:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create_topic(self, **kwargs):
            self.calls.append({"op": "create_topic", **kwargs})
            return ForumTopicCreateResult(topic=_topic(), created=True)

    service = Service()
    response = Response()

    payload = await create_topic_internal(
        body=CreateTopicRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-internal-topic",
            title=" Topic title ",
            body=" Topic description ",
        ),
        request=_request("POST", "/api/v1/bbs/topics"),
        response=response,
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.data is not None
    assert payload.data.model_dump() == {"topic_id": "topic_1"}
    # Author is the body-declared human; no topic_type key is forwarded.
    assert service.calls == [
        {
            "op": "create_topic",
            "author_type": "HUMAN",
            "author_id": "user-a",
            "client_request_id": "req-internal-topic",
            "title": " Topic title ",
            "body": " Topic description ",
        }
    ]


@pytest.mark.asyncio
async def test_internal_topic_create_replay_returns_200_with_original_id():
    class ReplayService:
        def create_topic(self, **kwargs):
            return ForumTopicCreateResult(topic=_topic(), created=False)

    response = Response()
    payload = await create_topic_internal(
        body=CreateTopicRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-internal-topic",
            title="Topic title",
            body="Topic description",
        ),
        request=_request("POST", "/api/v1/bbs/topics"),
        response=response,
        service=ReplayService(),
    )

    assert response.status_code == 200
    assert payload.code == 200000
    assert payload.data is not None
    assert payload.data.topic_id == "topic_1"


@pytest.mark.asyncio
async def test_internal_reply_create_passes_body_author_as_bot_author():
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
        body=CreateReplyRequestUnified(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="request-2",
            body="Reply body",
        ),
        topic_id="topic_1",
        request=_request("POST", "/api/v1/bbs/topics/topic_1/posts"),
        response=response,
        service=Service(),
    )

    assert response.status_code == 200
    assert payload.data is not None and payload.data.post_id == "post_1"


@pytest.mark.asyncio
async def test_internal_reply_create_passes_body_author_as_human_author():
    class Service:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create_reply(self, **kwargs):
            self.calls.append({"op": "create_reply", **kwargs})
            return ForumReplyCreateResult(post=_post(), created=True)

    service = Service()
    response = Response()

    payload = await create_reply_internal(
        body=CreateReplyRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-internal-reply",
            body=" Reply body ",
        ),
        topic_id="topic_1",
        request=_request("POST", "/api/v1/bbs/topics/topic_1/posts"),
        response=response,
        service=service,
    )

    assert response.status_code == 201
    assert payload.data is not None
    assert payload.data.model_dump() == {"post_id": "post_1"}
    assert service.calls == [
        {
            "op": "create_reply",
            "topic_id": "topic_1",
            "author_type": "HUMAN",
            "author_id": "user-a",
            "client_request_id": "req-internal-reply",
            "body": " Reply body ",
        }
    ]


# ---------------------------------------------------------------------------
# BBS Topic close — internal /api/v1/bbs mirror tests
# ---------------------------------------------------------------------------
# The unified close route lets the author named in the body close a Topic only
# if that declared author matches the stored topic author (both kind and id,
# after the same case-fold and trim the create path applied). Mismatches
# surface as 403; a missing Topic as 404; a LOCKED Topic as 409.


@pytest.mark.asyncio
async def test_internal_close_topic_closes_when_declared_author_matches():
    class Service:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def get_topic(self, **kwargs):
            self.calls.append({"op": "get_topic", **kwargs})
            return dataclasses.replace(_topic(), author_type="HUMAN", author_id="user-a")

        def close_topic(self, **kwargs):
            self.calls.append({"op": "close_topic", **kwargs})
            return dataclasses.replace(
                _topic(), author_type="HUMAN", author_id="user-a", status="CLOSED"
            )

    service = Service()
    payload = await close_topic_internal(
        body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
        topic_id="topic_1",
        request=_request("POST", "/api/v1/bbs/topics/topic_1/close"),
        service=service,
    )

    assert service.calls == [
        {"op": "get_topic", "topic_id": "topic_1"},
        {"op": "close_topic", "topic_id": "topic_1"},
    ]
    assert payload.data is not None
    assert payload.data.model_dump() == {"topic_id": "topic_1", "status": "CLOSED"}


@pytest.mark.asyncio
async def test_internal_close_topic_refuses_when_author_id_mismatch():
    other_topic = dataclasses.replace(_topic(), author_type="HUMAN", author_id="user-b")

    class Service:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def get_topic(self, **kwargs):
            self.calls.append({"op": "get_topic", **kwargs})
            return other_topic

        def close_topic(self, **kwargs):
            raise AssertionError("close_topic reached without an authorising get")

    service = Service()
    with pytest.raises(Forbidden):
        await close_topic_internal(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_1",
            request=_request("POST", "/api/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [{"op": "get_topic", "topic_id": "topic_1"}]


@pytest.mark.asyncio
async def test_internal_close_topic_refuses_when_author_kind_mismatch():
    bot_topic = _topic()  # author_type=BOT, author_id=bot-a by default

    class Service:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def get_topic(self, **kwargs):
            self.calls.append({"op": "get_topic", **kwargs})
            return bot_topic

        def close_topic(self, **kwargs):
            raise AssertionError("close_topic reached for a BOT-authored topic")

    service = Service()
    with pytest.raises(Forbidden):
        await close_topic_internal(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_1",
            request=_request("POST", "/api/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [{"op": "get_topic", "topic_id": "topic_1"}]


@pytest.mark.asyncio
async def test_internal_close_topic_surfaces_not_found_when_topic_missing():
    class MissingService:
        def get_topic(self, **kwargs):
            raise NotFound("topic not found")

        def close_topic(self, **kwargs):
            raise AssertionError("close_topic reached without an authorising get")

    with pytest.raises(NotFound):
        await close_topic_internal(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_missing",
            request=_request("POST", "/api/v1/bbs/topics/topic_missing/close"),
            service=MissingService(),
        )


@pytest.mark.asyncio
async def test_internal_close_topic_surfaces_conflict_when_topic_locked():
    class Service:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def get_topic(self, **kwargs):
            self.calls.append({"op": "get_topic", **kwargs})
            return _topic()  # author_type=BOT, author_id=bot-a — declared author matches

        def close_topic(self, **kwargs):
            self.calls.append({"op": "close_topic", **kwargs})
            raise Conflict("topic is LOCKED, cannot be closed")

    service = Service()
    with pytest.raises(Conflict):
        await close_topic_internal(
            body=CloseTopicRequestUnified(author_type="BOT", author_id="bot-a"),
            topic_id="topic_1",
            request=_request("POST", "/api/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [
        {"op": "get_topic", "topic_id": "topic_1"},
        {"op": "close_topic", "topic_id": "topic_1"},
    ]
