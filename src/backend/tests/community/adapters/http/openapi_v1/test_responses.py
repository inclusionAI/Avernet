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


# ── Envelope shape (Track C) ─────────────────────────────────────────────────


def test_envelope_has_exactly_the_four_documented_fields():
    """Regression guard: the success envelope stays four keys.

    A ``warning`` field was added here and then removed. Across both OSS engines
    the only *limited* capability this public surface can reach is
    ``SESSION_CREATE`` on claude_code, whose caveat describes how the session key
    is established rather than a degraded result — so the field would have been
    permanently empty on 15 of 16 engine-runtime endpoints and on all six other
    categories. Engine caveats are logged server-side in
    ``core/engine_runtime/relay.py``; the engine-capabilities endpoint is where a
    caller discovers which capabilities its bot serves with a limitation.
    """
    from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope

    assert set(Envelope.model_fields) == {"code", "message", "data", "request_id"}
    assert set(envelope({"x": 1}, _request()).model_dump()) == set(
        Envelope.model_fields
    )


def test_501_and_504_are_scoped_to_the_engine_runtime_groups():
    """Only the engine-runtime routes can return these, so only they document them.

    ``ERROR_RESPONSES`` is applied surface-wide in ``build_public_router`` and
    ``test_openapi_error_schema`` asserts every operation documents every status
    in it. Putting 501/504 there would make the six already-shipped categories
    advertise failures they cannot produce.
    """
    from agentclaw.community.adapters.http.openapi_v1.contracts import (
        ENGINE_RUNTIME_ERROR_RESPONSES,
        ERROR_RESPONSES,
        ErrorEnvelope,
    )

    for status in (501, 504):
        assert status not in ERROR_RESPONSES, (
            f"{status} must not be surface-wide — shipped categories cannot return it"
        )
        assert ENGINE_RUNTIME_ERROR_RESPONSES[status]["model"] is ErrorEnvelope

    # The per-group dict is a superset: engine-runtime routes still document
    # everything the rest of the surface does.
    assert set(ERROR_RESPONSES) <= set(ENGINE_RUNTIME_ERROR_RESPONSES)


def test_error_body_matches_the_documented_error_model():
    """Failure bodies serialise ``ErrorEnvelope``, the model ``ERROR_RESPONSES``
    actually documents, rather than ``Envelope``."""
    import json

    from agentclaw.community.adapters.http.openapi_v1.contracts import ErrorEnvelope
    from agentclaw.community.adapters.http.openapi_v1.responses import error_response

    resp = error_response(404, "Not found", _request())
    body = json.loads(bytes(resp.body))
    assert set(body) == set(ErrorEnvelope.model_fields)


# ── Engine-runtime error mapping (Track C, Task 6) ────────────────────────────


def _lookup(exc: Exception) -> tuple[int, str]:
    """Resolve exactly as ``envelope_errors`` does: first isinstance wins."""
    from agentclaw.community.adapters.http.openapi_v1.responses import ENVELOPE_ERRORS

    for error_type, mapped in ENVELOPE_ERRORS.items():
        if isinstance(exc, error_type):
            return mapped
    raise AssertionError(f"{type(exc).__name__} is unmapped")


@pytest.mark.parametrize(
    ("exc_name", "expected_status"),
    [
        ("EngineBotTypeNotSupportedError", 501),
        ("EngineCapabilityUnsupportedError", 501),
        ("EngineDeviceNotReadyError", 409),
        ("EngineResourceNotFoundError", 404),
        ("EngineUpstreamError", 502),
    ],
)
def test_engine_runtime_errors_map_to_their_own_status(exc_name, expected_status):
    """Each leaf resolves to itself, not to a base listed later."""
    import agentclaw.community.core.engine_runtime.errors as errs

    status, _ = _lookup(getattr(errs, exc_name)("boom"))
    assert status == expected_status


def test_engine_runtime_base_does_not_swallow_its_leaves():
    """``EngineRuntimeError`` is the base of all four; it must be listed last.

    This is the "map the base class last" trap from the Track B gotchas, and
    Track C introduces two base/leaf pairs at once — so it is asserted, not
    trusted.
    """
    from agentclaw.community.adapters.http.openapi_v1.responses import ENVELOPE_ERRORS
    from agentclaw.community.core.engine_runtime.errors import (
        EngineCapabilityUnsupportedError,
        EngineDeviceNotReadyError,
        EngineResourceNotFoundError,
        EngineRuntimeError,
        EngineUpstreamError,
    )

    order = list(ENVELOPE_ERRORS)
    base = order.index(EngineRuntimeError)
    for leaf in (
        EngineCapabilityUnsupportedError,
        EngineDeviceNotReadyError,
        EngineResourceNotFoundError,
        EngineUpstreamError,
    ):
        assert order.index(leaf) < base, f"{leaf.__name__} listed after its base"


def test_transport_errors_are_siblings_and_map_independently():
    """They are NOT a hierarchy, so each needs its own entry.

    ``DeviceAdapterTimeoutError`` extends ``TimeoutError``; the other two are
    independent ``ValueError`` subclasses. Asserted because assuming a
    base/leaf relationship here would justify a wrong "fix" to the mapping
    order — and because if the transport ever *did* introduce one, the missing
    ordering rule would become a real bug this test would catch.
    """
    from agentclaw.community.plugin_api.device_adapter_transport import (
        DeviceAdapterEndpointNotFoundError,
        DeviceAdapterHTTPStatusError,
        DeviceAdapterTimeoutError,
    )

    assert not issubclass(
        DeviceAdapterEndpointNotFoundError, DeviceAdapterHTTPStatusError
    )
    assert not issubclass(
        DeviceAdapterHTTPStatusError, DeviceAdapterEndpointNotFoundError
    )
    assert not issubclass(DeviceAdapterTimeoutError, ValueError)

    assert _lookup(DeviceAdapterEndpointNotFoundError("gone"))[0] == 404
    assert _lookup(DeviceAdapterHTTPStatusError(503, "busy"))[0] == 502


def test_transport_timeout_maps_to_504():
    from agentclaw.community.plugin_api.device_adapter_transport import (
        DeviceAdapterTimeoutError,
    )

    assert _lookup(DeviceAdapterTimeoutError("slow"))[0] == 504


def test_the_two_501s_say_different_things():
    """They answer different questions and must not be merged.

    One is "your bot's engine does not offer this", which the capabilities
    endpoint can confirm; the other is "this operation is not offered for your
    bot's type", which capabilities cannot tell you.
    """
    from agentclaw.community.core.engine_runtime.errors import (
        EngineBotTypeNotSupportedError,
        EngineCapabilityUnsupportedError,
    )

    _, capability_msg = _lookup(EngineCapabilityUnsupportedError("x"))
    _, bot_type_msg = _lookup(EngineBotTypeNotSupportedError("x"))
    assert capability_msg != bot_type_msg
    assert "capabilities" in capability_msg
    assert "bot type" in bot_type_msg


def test_engine_not_found_is_byte_identical_to_the_other_404s():
    """Otherwise a caller could distinguish "gone" from "not yours"."""
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotNotFoundError,
    )
    from agentclaw.community.core.engine_runtime.errors import (
        EngineResourceNotFoundError,
    )

    assert _lookup(EngineResourceNotFoundError("x")) == _lookup(BotNotFoundError("y"))


def test_engine_runtime_messages_are_fixed_not_exception_text():
    """No engine-side detail may reach an external caller."""
    import agentclaw.community.core.engine_runtime.errors as errs

    leak = "sandbox-7f3a at 10.0.0.4:20003 token=abc"
    for name in (
        "EngineBotTypeNotSupportedError",
        "EngineCapabilityUnsupportedError",
        "EngineDeviceNotReadyError",
        "EngineResourceNotFoundError",
        "EngineUpstreamError",
    ):
        _, message = _lookup(getattr(errs, name)(leak))
        assert leak not in message
        assert message.isascii(), f"{name}: public messages are always English"

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
