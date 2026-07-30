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
    # Body carries the enveloped error: null data, 6-digit code, trace id, and a
    # FIXED message (never the raw exception text — no internal leak).
    import json

    body = json.loads(bytes(resp.body))
    assert body["code"] == 404000
    assert body["data"] is None
    assert body["message"] == "Not found"
    assert body["request_id"] == "trace-123"


@pytest.mark.asyncio
async def test_not_found_and_permission_errors_are_indistinguishable():
    """The 404-masking guarantee: differing internal text must not leak."""
    import json

    @envelope_errors
    async def not_found(request: Request):
        raise BotNotFoundError("Bot not found: b-123")

    @envelope_errors
    async def permission(request: Request):
        raise BotPermissionError("架构师 Bot 不存在或非本人所有: b-123")

    nf = await not_found(request=_request())
    perm = await permission(request=_request())
    assert nf.status_code == perm.status_code == 404
    # Byte-for-byte identical bodies — a caller cannot tell the two cases apart.
    assert json.loads(bytes(nf.body)) == json.loads(bytes(perm.body))
    assert "b-123" not in json.loads(bytes(perm.body))["message"]


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


# ── MCP category error mappings (Track B mcp, Task 6) ───────────────


@pytest.mark.asyncio
async def test_mcp_errors_map_to_status_and_fixed_message():
    """Every MCP domain error round-trips to its status + fixed public message."""
    import json

    from agentclaw.community.core.mcp.errors import (
        McpConfigValueError,
        McpHeadersInvalidError,
        McpMarketUnavailableError,
        McpServerNotFoundError,
        McpSyncFailedError,
    )

    cases = [
        # Internal-language / identifier-bearing text that must NOT leak.
        (McpServerNotFoundError("mcp.secret.server"), 404, "Not found"),
        (McpHeadersInvalidError("Header 键不能为空"), 400, "Invalid MCP headers"),
        (McpConfigValueError("endpoint_env must be PROD or PRE"), 400, "Invalid MCP configuration"),
        (McpSyncFailedError("bot b-123 device down"), 502, "Device sync failed"),
        (McpMarketUnavailableError("upstream 500"), 502, "MCP service error"),
    ]
    for exc, status, message in cases:

        @envelope_errors
        async def handler(request: Request, _exc=exc):
            raise _exc

        resp = await handler(request=_request())
        assert resp.status_code == status
        body = json.loads(bytes(resp.body))
        assert body["message"] == message
        assert body["code"] == status * 1000
        assert body["data"] is None
        # The raw exception text (identifiers / Chinese) never reaches the caller.
        assert str(exc) not in body["message"]


@pytest.mark.asyncio
async def test_mcp_not_found_is_indistinguishable_from_bots_not_found():
    """A missing MCP server and a masked bot 404 answer byte-identical bodies."""
    import json

    from agentclaw.community.core.mcp.errors import McpServerNotFoundError

    @envelope_errors
    async def mcp_missing(request: Request):
        raise McpServerNotFoundError("mcp.x")

    @envelope_errors
    async def bot_missing(request: Request):
        raise BotNotFoundError("Bot not found: b-1")

    a = await mcp_missing(request=_request())
    b = await bot_missing(request=_request())
    assert a.status_code == b.status_code == 404
    assert json.loads(bytes(a.body)) == json.loads(bytes(b.body))
