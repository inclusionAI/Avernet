"""Unit tests for the public-API response/envelope helper (Track B, Task 1)."""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_ACCEPTED,
    CODE_CREATED,
    CODE_OK,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    ClusterMismatchError,
    accepted,
    created,
    deleted,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotPermissionError,
)


def _request(trace_id: str | None = "trace-123") -> Request:
    """A minimal ASGI request; ``trace_id`` on state unless explicitly omitted."""
    state: dict[str, object] = {}
    if trace_id is not None:
        state["trace_id"] = trace_id
    return Request({"type": "http", "state": state})


def test_envelope_success_fields():
    env = envelope({"x": 1}, _request())
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data == {"x": 1}
    assert env.request_id == "trace-123"


def test_envelope_trace_id_falls_back_to_empty():
    assert envelope(None, _request(trace_id=None)).request_id == ""


def test_page_wraps_items_and_total():
    env = page(7, [{"id": "a"}], _request())
    assert env.code == CODE_OK
    assert env.data.total == 7
    assert env.data.items == [{"id": "a"}]


def test_created_and_accepted_and_deleted_codes():
    assert created({"id": "a"}, _request()).code == CODE_CREATED
    assert accepted({"id": "a"}, _request()).code == CODE_ACCEPTED
    del_env = deleted(_request())
    assert del_env.data.deleted is True
    assert del_env.code == CODE_OK


@pytest.mark.asyncio
async def test_envelope_errors_maps_domain_error():
    @envelope_errors
    async def handler(request: Request):
        raise BotNotFoundError("no such bot")

    resp = await handler(request=_request())
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 404
    # Body carries the enveloped error: null data, 6-digit code, trace id.
    import json

    body = json.loads(bytes(resp.body))
    assert body["code"] == 404000
    assert body["data"] is None
    assert body["message"] == "no such bot"
    assert body["request_id"] == "trace-123"


@pytest.mark.asyncio
async def test_envelope_errors_maps_permission_to_404():
    @envelope_errors
    async def handler(request: Request):
        raise BotPermissionError("not yours")

    resp = await handler(request=_request())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_envelope_errors_maps_cluster_mismatch_to_400():
    @envelope_errors
    async def handler(request: Request):
        raise ClusterMismatchError("engine/cluster mismatch")

    resp = await handler(request=_request())
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_envelope_errors_passes_through_unmapped():
    @envelope_errors
    async def handler(request: Request):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await handler(request=_request())
