"""The published schema documents the error envelope (R6/F28).

Every failure on this surface returns an ``Envelope``, but the routes declared
only their success models, so FastAPI published its default
``HTTPValidationError`` for 422 and nothing at all for the mapped 4xx/5xx
responses. Clients generated from that schema were handed a wire contract that
disagrees with what the runtime returns.

Asserted against the real generated document rather than the declaration, so
this catches a change in how FastAPI merges router-level ``responses`` as well
as a change to the mapping itself.
"""

from __future__ import annotations

from fastapi import FastAPI

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.contracts import ERROR_RESPONSES

_ERROR_REF = "#/components/schemas/ErrorEnvelope"

# (path, status) pairs that deliberately document a data-bearing model instead
# of the shared null-data envelope. Terminal authorization states are reported
# as failures, but the state itself is what the caller acts on, so it stays in
# ``data`` and the route declares that shape. Every other error stays uniform.
_DOCUMENTED_DATA_BEARING_ERRORS = {
    ("/openapi/v1/bots/{bot_id}/auth-status", 400),
}


def _schema() -> dict:
    app = FastAPI()
    app.include_router(build_public_router())
    return app.openapi()


def _json_ref(operation: dict, status: str) -> str | None:
    content = operation["responses"][status].get("content") or {}
    return content.get("application/json", {}).get("schema", {}).get("$ref")


def _operations(schema: dict):
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            yield path, method, operation


def test_every_public_operation_documents_the_error_envelope():
    schema = _schema()
    operations = list(_operations(schema))
    assert operations, "no public operations found — the fixture is wrong"

    for path, method, operation in operations:
        for status in ERROR_RESPONSES:
            where = f"{method.upper()} {path} -> {status}"
            assert str(status) in operation["responses"], f"{where} undocumented"
            if (path, status) in _DOCUMENTED_DATA_BEARING_ERRORS:
                continue  # declares a richer model on purpose — see F38 below
            assert _json_ref(operation, str(status)) == _ERROR_REF, where


def test_validation_response_is_the_envelope_not_fastapis_default():
    """422 must be overridden — the app translates it to the envelope."""
    schema = _schema()
    for path, method, operation in _operations(schema):
        ref = _json_ref(operation, "422")
        assert ref == _ERROR_REF, f"{method.upper()} {path} still publishes {ref}"
    assert "HTTPValidationError" not in (schema["components"]["schemas"]), (
        "FastAPI's default validation model is still published on this surface"
    )


def test_success_responses_are_untouched():
    """The error declarations must not displace the per-route success models."""
    schema = _schema()
    get_bot = schema["paths"]["/openapi/v1/bots/{bot_id}"]["get"]
    assert "Envelope" in (_json_ref(get_bot, "200") or "")

    # The create route's own 202 declaration survives the merge.
    create = schema["paths"]["/openapi/v1/bots"]["post"]
    assert "202" in create["responses"]
    assert "201" in create["responses"]


def test_error_envelope_pins_data_to_null():
    """A generated client should see ``data`` as always-null on failures."""
    model = _schema()["components"]["schemas"]["ErrorEnvelope"]
    assert set(model["required"]) >= {"code", "message", "request_id"}
    assert model["properties"]["data"].get("type") == "null"


# ----- R9/F38: the one documented exception to ErrorEnvelope ----------------


def test_auth_status_400_documents_the_status_payload():
    """The terminal-auth 400 returns data, so it must not be typed as null.

    Every other 400 on this surface is an ``ErrorEnvelope`` with ``data: null``.
    This route reports a terminal authorization state as a failure but keeps the
    state in ``data`` — the actionable part — so a client generated against the
    surface-wide declaration would deserialize it against the wrong model.
    """
    schema = _schema()
    op = schema["paths"]["/openapi/v1/bots/{bot_id}/auth-status"]["get"]
    ref = _json_ref(op, "400")
    assert ref != _ERROR_REF, "still documented as the null-data error envelope"
    assert "BotAuthStatus" in ref, ref


def test_other_routes_keep_the_shared_error_envelope_for_400():
    """The override is scoped to that one route, not leaked surface-wide."""
    schema = _schema()
    for path, method, op in _operations(schema):
        if path.endswith("/auth-status"):
            continue
        assert _json_ref(op, "400") == _ERROR_REF, f"{method.upper()} {path}"
