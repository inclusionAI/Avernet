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


# ── Envelope.warning (Track C, Task 1) ────────────────────────────────────────
#
# Added for the engine-runtime surface: an engine may declare a capability as
# supported-*with-a-caveat* and return a human-readable warning alongside a
# real payload. The envelope had nowhere to put it, so the caveat was being
# dropped. Additive and defaulted, so the six pre-existing categories are
# unaffected beyond serialising an extra empty key.


def test_envelope_warning_defaults_to_empty():
    """Every existing caller builds envelopes without a warning."""
    env = envelope({"x": 1}, _request())
    assert env.warning == ""
    assert env.model_dump()["warning"] == ""


def test_envelope_warning_round_trips_when_set():
    env = envelope({"x": 1}, _request(), warning="served with a limitation")
    assert env.model_dump()["warning"] == "served with a limitation"


def test_page_envelope_carries_the_warning_field():
    """Page responses share the Envelope, so they inherit the field."""
    env = page(2, [{"a": 1}], _request())
    assert env.model_dump()["warning"] == ""


def test_error_envelope_has_no_warning():
    """A failed request has no partial payload to caveat.

    ``ErrorEnvelope`` is the documented error model on every public route; adding
    a field there would promise callers something no error path populates.
    """
    from agentclaw.community.adapters.http.openapi_v1.contracts import ErrorEnvelope

    assert "warning" not in ErrorEnvelope.model_fields


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


def test_envelope_builder_accepts_a_warning():
    """The field needs a supported builder path, not attribute mutation."""
    env = envelope({"x": 1}, _request(), warning="served with a limitation")
    assert env.warning == "served with a limitation"
    assert page(1, [{"x": 1}], _request(), warning="w").warning == "w"


def test_error_body_matches_the_documented_error_model():
    """Failure bodies serialise ``ErrorEnvelope``, not ``Envelope``.

    Once ``Envelope`` gained ``warning`` the two shapes diverged, and
    ``_error_response`` was building the wrong one — every error body carried a
    ``warning: ""`` key that ``ERROR_RESPONSES`` does not document. Regression
    guard: the emitted key set must equal the documented model's field set.
    """
    import json

    from agentclaw.community.adapters.http.openapi_v1.contracts import ErrorEnvelope
    from agentclaw.community.adapters.http.openapi_v1.responses import error_response

    resp = error_response(404, "Not found", _request())
    body = json.loads(bytes(resp.body))
    assert set(body) == set(ErrorEnvelope.model_fields)
    assert "warning" not in body
