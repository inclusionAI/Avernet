from __future__ import annotations

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.responses import mapped_error_response
from agentclaw.community.core.task.domain.errors import DispatchInProgress


def test_dispatch_in_progress_maps_conflict_with_retry_after() -> None:
    request = Request({"type": "http", "headers": []})
    response = mapped_error_response(DispatchInProgress(), request)

    assert response is not None
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "10"
    assert b'"message":"Dispatch in progress"' in bytes(response.body)


