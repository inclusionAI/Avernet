from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.openapi_v1.bbs.router import (
    close_topic_unified,
    create_reply_unified,
    create_topic_unified,
)
from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    CloseTopicRequestUnified,
    CreateReplyRequestUnified,
    CreateTopicRequestUnified,
)
from agentclaw.community.core.errors import Conflict, Forbidden, NotFound
from agentclaw.community.core.forum.models import (
    ForumPostRecord,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicRecord,
)

_NOW = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-unified"
    return request


def _topic(
    topic_id="topic_1",
    *,
    author_type="HUMAN",
    author_id="user-a",
    status="OPEN",
) -> ForumTopicRecord:
    return ForumTopicRecord(
        topic_id=topic_id,
        author_type=author_type,
        author_id=author_id,
        title="Topic title",
        body="Topic description",
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _topic_result(created: bool, topic=None) -> ForumTopicCreateResult:
    return ForumTopicCreateResult(topic=topic or _topic(), created=created)


def _reply_result(created: bool) -> ForumReplyCreateResult:
    return ForumReplyCreateResult(
        post=ForumPostRecord(
            post_id="post_2",
            topic_id="topic_1",
            author_type="BOT",
            author_id="bot-a",
            body="Reply body",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        created=created,
    )


def _write_service(result):
    class RecordedService:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create_topic(self, **kwargs):
            self.calls.append({"op": "create_topic", **kwargs})
            return result

        def create_reply(self, **kwargs):
            self.calls.append({"op": "create_reply", **kwargs})
            return result

    return RecordedService()


# ---------------------------------------------------------------------------
# create_topic_unified — author declared in the body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_topic_unified_records_declared_human_author():
    service = _write_service(_topic_result(True))
    response = Response()

    payload = await create_topic_unified(
        body=CreateTopicRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-1",
            title="t",
            body="b",
        ),
        request=_request("POST", "/openapi/v1/bbs/topics"),
        response=response,
        service=service,
    )

    assert response.status_code == 201
    assert service.calls == [
        {
            "op": "create_topic",
            "author_type": "HUMAN",
            "author_id": "user-a",
            "client_request_id": "req-1",
            "title": "t",
            "body": "b",
        }
    ]
    assert payload.data is not None and payload.data.topic_id == "topic_1"


@pytest.mark.asyncio
async def test_create_topic_unified_records_declared_bot_author():
    service = _write_service(_topic_result(True))

    await create_topic_unified(
        body=CreateTopicRequestUnified(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-1",
            title="t",
            body="b",
        ),
        request=_request("POST", "/openapi/v1/bbs/topics"),
        response=Response(),
        service=service,
    )

    assert service.calls == [
        {
            "op": "create_topic",
            "author_type": "BOT",
            "author_id": "bot-a",
            "client_request_id": "req-1",
            "title": "t",
            "body": "b",
        }
    ]


@pytest.mark.asyncio
async def test_create_topic_unified_passes_author_type_through_verbatim():
    # author_type is a free-string field: ForumService does the .upper()
    # normalisation, so a lowercase value is recorded unchanged here.
    service = _write_service(_topic_result(True))

    await create_topic_unified(
        body=CreateTopicRequestUnified(
            author_type="human",
            author_id="user-a",
            client_request_id="req-1",
            title="t",
            body="b",
        ),
        request=_request("POST", "/openapi/v1/bbs/topics"),
        response=Response(),
        service=service,
    )

    assert service.calls[0]["author_type"] == "human"


@pytest.mark.asyncio
async def test_create_topic_unified_replay_returns_200_with_original_id():
    service = _write_service(_topic_result(False))
    response = Response()

    payload = await create_topic_unified(
        body=CreateTopicRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-1",
            title="t",
            body="b",
        ),
        request=_request("POST", "/openapi/v1/bbs/topics"),
        response=response,
        service=service,
    )

    assert response.status_code == 200
    assert payload.code == 200000


# ---------------------------------------------------------------------------
# create_reply_unified — author declared in the body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_reply_unified_records_declared_human_author():
    service = _write_service(_reply_result(True))
    response = Response()

    await create_reply_unified(
        body=CreateReplyRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-2",
            body="r",
        ),
        topic_id="topic_1",
        request=_request("POST", "/openapi/v1/bbs/topics/topic_1/posts"),
        response=response,
        service=service,
    )

    assert response.status_code == 201
    assert service.calls == [
        {
            "op": "create_reply",
            "topic_id": "topic_1",
            "author_type": "HUMAN",
            "author_id": "user-a",
            "client_request_id": "req-2",
            "body": "r",
        }
    ]


@pytest.mark.asyncio
async def test_create_reply_unified_records_declared_bot_author():
    service = _write_service(_reply_result(True))

    await create_reply_unified(
        body=CreateReplyRequestUnified(
            author_type="BOT",
            author_id="bot-a",
            client_request_id="req-2",
            body="r",
        ),
        topic_id="topic_1",
        request=_request("POST", "/openapi/v1/bbs/topics/topic_1/posts"),
        response=Response(),
        service=service,
    )

    assert service.calls == [
        {
            "op": "create_reply",
            "topic_id": "topic_1",
            "author_type": "BOT",
            "author_id": "bot-a",
            "client_request_id": "req-2",
            "body": "r",
        }
    ]


@pytest.mark.asyncio
async def test_create_reply_unified_replay_returns_200():
    service = _write_service(_reply_result(False))
    response = Response()

    payload = await create_reply_unified(
        body=CreateReplyRequestUnified(
            author_type="HUMAN",
            author_id="user-a",
            client_request_id="req-2",
            body="r",
        ),
        topic_id="topic_1",
        request=_request("POST", "/openapi/v1/bbs/topics/topic_1/posts"),
        response=response,
        service=service,
    )

    assert response.status_code == 200
    assert payload.code == 200000


# ---------------------------------------------------------------------------
# close_topic_unified — author declared in the body, type-agnostic equality
# ---------------------------------------------------------------------------


def _close_service(topic, *, closed=None, raise_close=None):
    class CloseService:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def get_topic(self, **kwargs):
            self.calls.append({"op": "get_topic", **kwargs})
            return topic

        def close_topic(self, **kwargs):
            self.calls.append({"op": "close_topic", **kwargs})
            if raise_close is not None:
                raise raise_close
            return closed if closed is not None else topic

    return CloseService()


async def _close(
    author_type, author_id, *, topic, closed=None, raise_close=None
):
    service = _close_service(topic, closed=closed, raise_close=raise_close)
    payload = await close_topic_unified(
        body=CloseTopicRequestUnified(author_type=author_type, author_id=author_id),
        topic_id="topic_1",
        request=_request("POST", "/openapi/v1/bbs/topics/topic_1/close"),
        service=service,
    )
    return service, payload


@pytest.mark.asyncio
async def test_close_topic_unified_human_closes_own_topic():
    service, payload = await _close(
        "HUMAN",
        "user-a",
        topic=_topic(author_type="HUMAN", author_id="user-a"),
        closed=_topic(author_type="HUMAN", author_id="user-a", status="CLOSED"),
    )
    assert service.calls == [
        {"op": "get_topic", "topic_id": "topic_1"},
        {"op": "close_topic", "topic_id": "topic_1"},
    ]
    assert payload.data is not None
    assert payload.data.model_dump() == {"topic_id": "topic_1", "status": "CLOSED"}


@pytest.mark.asyncio
async def test_close_topic_unified_bot_closes_own_topic():
    service, payload = await _close(
        "BOT",
        "bot-a",
        topic=_topic(author_type="BOT", author_id="bot-a"),
        closed=_topic(author_type="BOT", author_id="bot-a", status="CLOSED"),
    )
    assert service.calls == [
        {"op": "get_topic", "topic_id": "topic_1"},
        {"op": "close_topic", "topic_id": "topic_1"},
    ]
    assert payload.data.model_dump() == {"topic_id": "topic_1", "status": "CLOSED"}


@pytest.mark.asyncio
async def test_close_topic_unified_case_folds_author_type_before_compare():
    # The create path upper-cases author_type before storing it, so the close
    # handler case-folds the declared value too: "human" still matches "HUMAN".
    service, _payload = await _close(
        "human",
        "user-a",
        topic=_topic(author_type="HUMAN", author_id="user-a"),
        closed=_topic(author_type="HUMAN", author_id="user-a", status="CLOSED"),
    )
    assert [c["op"] for c in service.calls] == ["get_topic", "close_topic"]


@pytest.mark.asyncio
async def test_close_topic_unified_trims_author_id_before_compare():
    # The create path strips author_id before storing it, so the close
    # comparison strips the declared value too: padded whitespace is not a 403.
    service, _payload = await _close(
        "HUMAN",
        "  user-a  ",
        topic=_topic(author_type="HUMAN", author_id="user-a"),
        closed=_topic(author_type="HUMAN", author_id="user-a", status="CLOSED"),
    )
    assert [c["op"] for c in service.calls] == ["get_topic", "close_topic"]


@pytest.mark.asyncio
async def test_close_topic_unified_human_refuses_bot_topic():
    service = _close_service(_topic(author_type="BOT", author_id="bot-a"))
    with pytest.raises(Forbidden):
        await close_topic_unified(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_1",
            request=_request("POST", "/openapi/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [{"op": "get_topic", "topic_id": "topic_1"}]


@pytest.mark.asyncio
async def test_close_topic_unified_bot_refuses_human_topic():
    service = _close_service(_topic(author_type="HUMAN", author_id="user-a"))
    with pytest.raises(Forbidden):
        await close_topic_unified(
            body=CloseTopicRequestUnified(author_type="BOT", author_id="bot-a"),
            topic_id="topic_1",
            request=_request("POST", "/openapi/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [{"op": "get_topic", "topic_id": "topic_1"}]


@pytest.mark.asyncio
async def test_close_topic_unified_refuses_wrong_human_author():
    service = _close_service(_topic(author_type="HUMAN", author_id="user-b"))
    with pytest.raises(Forbidden):
        await close_topic_unified(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_1",
            request=_request("POST", "/openapi/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [{"op": "get_topic", "topic_id": "topic_1"}]


@pytest.mark.asyncio
async def test_close_topic_unified_refuses_wrong_bot_author():
    service = _close_service(_topic(author_type="BOT", author_id="bot-b"))
    with pytest.raises(Forbidden):
        await close_topic_unified(
            body=CloseTopicRequestUnified(author_type="BOT", author_id="bot-a"),
            topic_id="topic_1",
            request=_request("POST", "/openapi/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [{"op": "get_topic", "topic_id": "topic_1"}]


@pytest.mark.asyncio
async def test_close_topic_unified_surfaces_not_found():
    class MissingService:
        def get_topic(self, **kwargs):
            raise NotFound("topic not found")

        def close_topic(self, **kwargs):
            raise AssertionError("close_topic reached without an authorising get")

    with pytest.raises(NotFound):
        await close_topic_unified(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_missing",
            request=_request("POST", "/openapi/v1/bbs/topics/topic_missing/close"),
            service=MissingService(),
        )


@pytest.mark.asyncio
async def test_close_topic_unified_surfaces_conflict_when_locked():
    service = _close_service(
        _topic(author_type="HUMAN", author_id="user-a"),
        raise_close=Conflict("topic is LOCKED, cannot be closed"),
    )
    with pytest.raises(Conflict):
        await close_topic_unified(
            body=CloseTopicRequestUnified(author_type="HUMAN", author_id="user-a"),
            topic_id="topic_1",
            request=_request("POST", "/openapi/v1/bbs/topics/topic_1/close"),
            service=service,
        )
    assert service.calls == [
        {"op": "get_topic", "topic_id": "topic_1"},
        {"op": "close_topic", "topic_id": "topic_1"},
    ]
