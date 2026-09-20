from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.openapi_v1.bbs.router import (
    create_reply,
    create_topic,
)
from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    CreateReplyRequest,
    CreateTopicRequest,
)
from agentclaw.community.core.forum.models import (
    ForumPostRecord,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicRecord,
)


def _recorded_service(result: ForumTopicCreateResult | ForumReplyCreateResult):
    class RecordedService:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def create_topic(self, **kwargs):
            self.calls.append(kwargs)
            return result

        def create_reply(self, **kwargs):
            self.calls.append(kwargs)
            return result

    return RecordedService()


def _request() -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/openapi/v1/bots/bot-a/bbs/topics",
        }
    )


def _topic_result(created: bool) -> ForumTopicCreateResult:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    return ForumTopicCreateResult(
        topic=ForumTopicRecord(
            topic_id="topic_1",
            author_type="BOT",
            author_id="bot-a",
            title="Topic title",
            body="Topic description",
            status="OPEN",
            created_at=now,
            updated_at=now,
        ),
        created=created,
    )


def _reply_result(created: bool) -> ForumReplyCreateResult:
    now = datetime(2026, 9, 20, 8, 1, tzinfo=timezone.utc)
    return ForumReplyCreateResult(
        post=ForumPostRecord(
            post_id="post_2",
            topic_id="topic_1",
            author_type="BOT",
            author_id="bot-a",
            body="Reply body",
            created_at=now,
            updated_at=now,
        ),
        created=created,
    )


@pytest.mark.asyncio
async def test_create_topic_response_contract_and_path_author():
    service = _recorded_service(_topic_result(True))
    request = _request()
    request.state.trace_id = "trace-1"
    response = Response()

    payload = await create_topic(
        body=CreateTopicRequest(
            client_request_id="req-topic",
            title=" Topic title ",
            body=" Topic description ",
        ),
        bot_id="bot-a",
        owner_id="owner-a",
        request=request,
        response=response,
        service=service,
    )

    assert response.status_code == 201
    assert payload.code == 201000
    assert payload.data is not None
    assert payload.data.model_dump() == {
        "topic_id": "topic_1",
        "topic_type": "DISCUSSION",
    }
    assert service.calls == [
        {
            "author_type": "BOT",
            "author_id": "bot-a",
            "client_request_id": "req-topic",
            "title": " Topic title ",
            "body": " Topic description ",
            "topic_type": "DISCUSSION",
        }
    ]


@pytest.mark.asyncio
async def test_create_topic_replay_returns_original_id_with_200():
    service = _recorded_service(_topic_result(False))
    request = _request()
    request.state.trace_id = "trace-2"
    response = Response()

    payload = await create_topic(
        body=CreateTopicRequest(
            client_request_id="req-topic",
            title="Topic title",
            body="Topic description",
        ),
        bot_id="bot-a",
        owner_id="owner-a",
        request=request,
        response=response,
        service=service,
    )

    assert response.status_code == 200
    assert payload.code == 200000
    assert payload.data is not None
    assert payload.data.topic_id == "topic_1"


@pytest.mark.asyncio
async def test_create_reply_response_contract():
    service = _recorded_service(_reply_result(True))
    request = _request()
    request.state.trace_id = "trace-3"
    response = Response()

    payload = await create_reply(
        body=CreateReplyRequest(client_request_id="req-reply", body=" Reply body "),
        bot_id="bot-a",
        topic_id="topic_1",
        owner_id="owner-a",
        request=request,
        response=response,
        service=service,
    )

    assert response.status_code == 201
    assert payload.data is not None
    assert payload.data.model_dump() == {"post_id": "post_2"}
    assert service.calls == [
        {
            "topic_id": "topic_1",
            "author_type": "BOT",
            "author_id": "bot-a",
            "client_request_id": "req-reply",
            "body": " Reply body ",
        }
    ]


@pytest.mark.asyncio
async def test_create_reply_replay_returns_200_with_original_post():
    service = _recorded_service(_reply_result(False))
    request = _request()
    request.state.trace_id = "trace-4"
    response = Response()

    payload = await create_reply(
        body=CreateReplyRequest(client_request_id="req-reply", body="Reply body"),
        bot_id="bot-a",
        topic_id="topic_1",
        owner_id="owner-a",
        request=request,
        response=response,
        service=service,
    )

    assert response.status_code == 200
    assert payload.code == 200000
    assert payload.data is not None
    assert payload.data.post_id == "post_2"
