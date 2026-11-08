from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response
from pydantic import ValidationError

from agentclaw.community.adapters.http.task.bbs.digest_router import (
    create_digest_report_internal,
    report_digest_decision_internal,
    router,
)
from agentclaw.community.adapters.http.task.bbs.schemas import (
    DigestDecisionRequest,
    DigestReportCreateRequest,
)
from agentclaw.community.core.task.forum.digest.models import (
    ForumDigestReportBrief,
    ForumDigestReportCreateResult,
    ForumDigestReportDecisionResult,
    ForumDigestReportItem,
    ForumDigestReportRecord,
)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-bbs-digest"
    return request


def _record(*, status: str = "PENDING_CONFIRMATION"):
    now = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    return ForumDigestReportRecord(
        digest_id="bbsd_1",
        bot_id="bot-a",
        owner_user_id="111111",
        client_request_id="digest-req-1",
        brief=ForumDigestReportBrief(
            summary="Bot produced a whole-batch summary before submission.",
            highlights=("Confirmation is whole-batch",),
        ),
        items=(
            ForumDigestReportItem(
                source="BBS",
                topic_id="topic_1",
                title="Keep BBS rules",
                content="High-value BBS content needs owner confirmation.",
                reason="Affects retention boundaries",
                tags=("bbs",),
                retention_target="long_term_memory",
            ),
        ),
        status=status,
        notification_status="SUBMITTED",
        session_id="session-1",
        session_url=(
            "http://frontend.local/workspace/chat?bot=bot-a%3A111111&session=session-1"
        ),
        created_at=now,
        updated_at=now,
    )


def _create_body(**kwargs):
    payload = {
        "bot_id": "bot-a",
        "client_request_id": "digest-req-1",
        "brief": {
            "summary": "Bot produced a whole-batch summary before submission.",
            "highlights": ["Confirmation is whole-batch"],
        },
        "items": [
            {
                "source": "BBS",
                "topic_id": "topic_1",
                "title": "Keep BBS rules",
                "content": "High-value BBS content needs owner confirmation.",
                "reason": "Affects retention boundaries",
                "tags": ["bbs"],
                "retention_target": "long_term_memory",
            }
        ],
    }
    payload.update(kwargs)
    return DigestReportCreateRequest(**payload)


def test_digest_router_declares_exactly_two_internal_batches():
    assert {
        (method, route.path)
        for route in router.routes
        for method in route.methods
    } == {
        ("POST", "/api/v1/collaboration/tasks/bbs/digest-reports"),
        ("POST", "/api/v1/collaboration/tasks/bbs/digest-reports/{digest_id}/decisions"),
    }


@pytest.mark.asyncio
async def test_create_digest_report_delegates_and_returns_201():
    class Service:
        async def create_report(self, **kwargs):
            assert kwargs["bot_id"] == "bot-a"
            assert isinstance(kwargs["brief"], ForumDigestReportBrief)
            assert isinstance(kwargs["items"][0], ForumDigestReportItem)
            return ForumDigestReportCreateResult(
                digest=_record(), created=True
            )

    response = Response()
    payload = await create_digest_report_internal(
        body=_create_body(),
        request=_request(
            "POST", "/api/v1/collaboration/tasks/bbs/digest-reports"
        ),
        response=response,
        service=Service(),
    )

    assert response.status_code == 201
    assert payload.data is not None
    assert payload.data.digest_id == "bbsd_1"
    assert payload.data.status == "PENDING_CONFIRMATION"
    assert payload.data.session_url == (
        "http://frontend.local/workspace/chat?bot=bot-a%3A111111&session=session-1"
    )
    assert payload.data.notification.status == "SUBMITTED"


@pytest.mark.asyncio
async def test_digest_report_replay_returns_200_with_same_digest():
    class Service:
        async def create_report(self, **kwargs):
            return ForumDigestReportCreateResult(
                digest=_record(), created=False
            )

    response = Response()
    payload = await create_digest_report_internal(
        body=_create_body(),
        request=_request("POST", "/digest-reports"),
        response=response,
        service=Service(),
    )

    assert response.status_code == 200
    assert payload.data is not None
    assert payload.data.digest_id == "bbsd_1"


@pytest.mark.asyncio
async def test_report_decision_delegates_and_returns_final_state():
    class Service:
        async def report_decision(self, **kwargs):
            assert kwargs == {
                "digest_id": "bbsd_1",
                "bot_id": "bot-a",
                "decision": "CONFIRMED",
                "decision_client_request_id": "decision-req-1",
            }
            return ForumDigestReportDecisionResult(
                digest=_record(status="CONFIRMED"), changed=True
            )

    payload = await report_digest_decision_internal(
        body=DigestDecisionRequest(
            bot_id="bot-a",
            client_request_id="decision-req-1",
            decision="CONFIRMED",
        ),
        digest_id="bbsd_1",
        request=_request(
            "POST",
            "/api/v1/collaboration/tasks/bbs/digest-reports/bbsd_1/decisions",
        ),
        service=Service(),
    )

    assert payload.data is not None
    assert payload.data.digest_id == "bbsd_1"
    assert payload.data.status == "CONFIRMED"
    assert payload.data.changed is True


def test_create_request_requires_bot_reported_brief():
    payload = _create_body().model_dump()
    del payload["brief"]

    with pytest.raises(ValidationError):
        DigestReportCreateRequest(**payload)


def test_create_request_rejects_empty_brief_highlights():
    payload = _create_body().model_dump()
    payload["brief"]["highlights"] = []

    with pytest.raises(ValidationError):
        DigestReportCreateRequest(**payload)


def test_create_request_rejects_client_supplied_owner_and_session():
    with pytest.raises(ValidationError):
        _create_body(owner_user_id="111111", session_id="session-1")
