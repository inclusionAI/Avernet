"""Internal ``/api/v1/feedback`` surface — submit and list general feedback.

Mirrors ``openapi_v1/feedback/test_feedback_endpoints.py`` line for line in
behaviour: same schemas, same service stub, same envelope/page assertions. The
only difference is the import path — these handlers come from the internal
``http/feedback`` router — and the request path string is ``/api/v1/feedback``
rather than ``/openapi/v1/feedback``. Both surfaces share
``FeedbackServiceProtocol`` and the same ``Envelope`` builders, so the
recorded service-call args and the response shape are required to be identical.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.feedback.router import (
    create_feedback,
    list_feedback,
)
from agentclaw.community.adapters.http.openapi_v1.feedback.schemas import (
    CreateFeedbackRequest,
    FeedbackListItem,
)
from agentclaw.community.core.feedback.models import (
    FeedbackCreateResult,
    FeedbackPage,
    FeedbackRecord,
)

_NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-feedback-internal"
    return request


def _record(id=1, *, reporter_id="149844", module="bbs", content="note") -> FeedbackRecord:
    return FeedbackRecord(
        id=id,
        reporter_id=reporter_id,
        module=module,
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _create_result() -> FeedbackCreateResult:
    return FeedbackCreateResult(feedback=_record(id=7), created=True)


def _feedback_service():
    class RecordedService:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create_feedback(self, **kwargs):
            self.calls.append({"op": "create_feedback", **kwargs})
            return _create_result()

        def list_feedback(self, **kwargs):
            self.calls.append({"op": "list_feedback", **kwargs})
            return FeedbackPage(total=1, items=(_record(id=7),))

    return RecordedService()


class _PageParams:
    def __init__(self, page: int, page_size: int) -> None:
        self.page = page
        self.page_size = page_size


@pytest.mark.asyncio
async def test_create_feedback_returns_201_envelope_with_id():
    service = _feedback_service()

    payload = await create_feedback(
        body=CreateFeedbackRequest(reporter_id="149844", module="bbs", content="hi"),
        request=_request("POST", "/api/v1/feedback"),
        response=Response(),
        service=service,
    )

    assert payload.code == 201000
    assert payload.message == "Created"
    assert payload.data.id == 7
    assert service.calls == [
        {"op": "create_feedback", "reporter_id": "149844", "module": "bbs", "content": "hi"}
    ]


@pytest.mark.asyncio
async def test_create_feedback_envelope_carries_request_id():
    service = _feedback_service()
    payload = await create_feedback(
        body=CreateFeedbackRequest(reporter_id="u1", module="task", content="ok"),
        request=_request("POST", "/api/v1/feedback"),
        response=Response(),
        service=service,
    )

    assert payload.request_id == "trace-feedback-internal"


@pytest.mark.asyncio
async def test_list_feedback_returns_page_envelope_with_items():
    service = _feedback_service()

    payload = await list_feedback(
        request=_request("GET", "/api/v1/feedback"),
        page_params=_PageParams(page=1, page_size=20),
        module="bbs",
        reporter_id="149844",
        service=service,
    )

    assert payload.code == 200000
    assert payload.data.total == 1
    assert len(payload.data.items) == 1
    item = payload.data.items[0]
    assert isinstance(item, FeedbackListItem)
    assert item.id == 7
    assert item.module == "bbs"
    assert item.reporter_id == "149844"
    assert service.calls[0]["page"] == 1
    assert service.calls[0]["page_size"] == 20
    assert service.calls[0]["module"] == "bbs"
    assert service.calls[0]["reporter_id"] == "149844"


@pytest.mark.asyncio
async def test_list_feedback_passes_none_filters_when_absent():
    service = _feedback_service()

    await list_feedback(
        request=_request("GET", "/api/v1/feedback"),
        page_params=_PageParams(page=2, page_size=50),
        module=None,
        reporter_id=None,
        service=service,
    )

    assert service.calls[0]["module"] is None
    assert service.calls[0]["reporter_id"] is None
    assert service.calls[0]["page"] == 2
    assert service.calls[0]["page_size"] == 50
