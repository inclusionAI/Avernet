"""Public-surface validation failures answer with the standard Envelope (R1/F3).

FastAPI raises ``RequestValidationError`` *before* a handler runs, so the
routers' ``@envelope_errors`` decorator never sees it. The app registers a
translation so the public namespace still answers in its uniform shape, while
internal routes keep FastAPI's default ``{"detail": [...]}``. This exercises that
handler directly against both path shapes.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX
from agentclaw.community.adapters.http.openapi_v1.responses import (
    error_response,
    validation_message,
)


class _Body(BaseModel):
    required_field: str


def _app() -> TestClient:
    app = FastAPI()

    # Same registration the real app performs (adapters/http/app.py).
    @app.exception_handler(RequestValidationError)
    async def _handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith(PUBLIC_API_PREFIX):
            safe = [
                {"loc": e.get("loc"), "type": e.get("type"), "msg": e.get("msg")}
                for e in exc.errors()
            ]
            return error_response(422, validation_message(safe, request), request)
        return await request_validation_exception_handler(request, exc)

    @app.post(f"{PUBLIC_API_PREFIX}/bots")
    async def _public(  # pragma: no cover - never reached
        body: _Body, user_id: str, page_size: int = 20
    ):
        return {}

    @app.post("/api/bots")
    async def _internal(body: _Body):  # pragma: no cover - never reached
        return {}

    return TestClient(app)


def test_public_validation_error_is_enveloped():
    resp = _app().post(f"{PUBLIC_API_PREFIX}/bots?user_id=u-1", json={})
    assert resp.status_code == 422
    body = resp.json()
    # The uniform envelope, not FastAPI's {"detail": [...]}.
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == 422000
    assert body["data"] is None


# ----- the 422 says which field, not just "Invalid request" -----------------
#
# The detail was computed and then dropped: the handler built the safe
# loc/type/msg triple, logged it, and answered a bare "Invalid request". A caller
# outside this deployment cannot read that log, so a rejected request was
# undiagnosable from the response — two round trips through a server log to
# discover a missing query parameter and a missing body.


def test_message_names_the_missing_field():
    resp = _app().post(f"{PUBLIC_API_PREFIX}/bots", json={"required_field": "x"})
    assert resp.status_code == 422
    message = resp.json()["message"]
    assert "query.user_id" in message
    assert "Field required" in message


def test_message_names_every_failure_at_once():
    """One round trip has to be enough to fix the whole request."""
    resp = _app().post(f"{PUBLIC_API_PREFIX}/bots")
    message = resp.json()["message"]
    assert "query.user_id" in message
    assert "body" in message


def test_message_does_not_repeat_one_parameter():
    """``user_id`` is resolved twice — directly, and by the owner_id default.

    Both resolutions fail on a request that omits it, so the unfiltered list
    carried the same line twice with nothing to tell them apart.
    """
    from starlette.requests import Request as StarletteRequest

    errors = [
        {"loc": ("query", "user_id"), "type": "missing", "msg": "Field required"},
        {"loc": ("query", "user_id"), "type": "missing", "msg": "Field required"},
        {"loc": ("body",), "type": "missing", "msg": "Field required"},
    ]
    scope = {"type": "http", "headers": [], "method": "POST", "path": "/"}
    rendered = validation_message(errors, StarletteRequest(scope))
    assert rendered.count("query.user_id") == 1
    assert "body" in rendered


def test_form_encoded_body_says_so():
    """The failure a JSON body sent without the JSON header produces.

    curl's ``-d`` defaults to form encoding, so the body reaches the model as
    raw bytes and pydantic answers "Input should be a valid dictionary" — true,
    and no help at all in finding the header that caused it.
    """
    resp = _app().post(
        f"{PUBLIC_API_PREFIX}/bots?user_id=u-1", data={"required_field": "x"}
    )
    assert resp.status_code == 422
    assert "Content-Type: application/json" in resp.json()["message"]


def test_json_body_failure_does_not_mention_content_type():
    """The hint is for the header, so it must not fire on a well-formed body."""
    resp = _app().post(f"{PUBLIC_API_PREFIX}/bots?user_id=u-1", json={})
    assert "Content-Type" not in resp.json()["message"]


def test_message_is_bounded():
    """A caller cannot choose how many bytes the refusal costs."""
    from starlette.requests import Request as StarletteRequest

    errors = [
        {"loc": ("body", f"field_{i}"), "type": "missing", "msg": "Field required"}
        for i in range(500)
    ]
    scope = {"type": "http", "headers": [], "method": "POST", "path": "/"}
    message = validation_message(errors, StarletteRequest(scope))
    assert len(message) < 1200
    assert "and 490 more" in message


def test_message_carries_no_caller_input():
    """``input`` is dropped before rendering, as it is before logging."""
    resp = _app().post(
        f"{PUBLIC_API_PREFIX}/bots?user_id=u-1",
        json={"required_field": {"secret": "s3cret-value"}},
    )
    assert resp.status_code == 422
    assert "s3cret-value" not in resp.text


def test_internal_validation_error_keeps_fastapi_shape():
    """Scoping guard: existing internal clients must be unaffected."""
    resp = _app().post("/api/bots", json={})
    assert resp.status_code == 422
    assert "detail" in resp.json()


# ----- R5/F24: nothing escapes the envelope ---------------------------------
#
# Enumerating each new escapee in ENVELOPE_ERRORS is whack-a-mole (F11 mapped
# one of three siblings; F21 caught the other two; F24 found a transport failure
# that is not a domain error at all). The app's catch-all now answers the public
# prefix in the envelope, so the contract holds for exceptions nobody mapped.


def _backstop_app() -> TestClient:
    from agentclaw.community.adapters.http.openapi_v1.responses import (
        is_public_api,
        unmapped_error_response,
    )

    app = FastAPI()

    # Same registration the real app performs (adapters/http/app.py).
    @app.exception_handler(Exception)
    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        if is_public_api(request):
            return unmapped_error_response(500, request)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    @app.get(f"{PUBLIC_API_PREFIX}/bots/boom")
    async def _public():
        # Stands in for httpx.HTTPStatusError out of a device file-transfer:
        # not a BotServiceError, not a DomainError, mapped nowhere.
        raise RuntimeError("device rejected the upload")

    @app.get("/api/bots/boom")
    async def _internal():
        raise RuntimeError("device rejected the upload")

    return TestClient(app, raise_server_exceptions=False)


def test_unmapped_public_exception_is_enveloped():
    resp = _backstop_app().get(f"{PUBLIC_API_PREFIX}/bots/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == 500000
    assert body["data"] is None
    # The reason phrase, never the exception's own text.
    assert body["message"] == "Internal Server Error"
    assert "device rejected" not in str(body)


def test_unmapped_internal_exception_keeps_detail_shape():
    """Scoping guard: existing internal clients must be unaffected."""
    resp = _backstop_app().get("/api/bots/boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal Server Error"}


# ----- R6/F30: routing errors are enveloped too -----------------------------
#
# Starlette raises these before any router is reached — an unknown public path
# (404) or a wrong method on a known one (405) — so neither @envelope_errors nor
# the generic catch-all sees them. They are the first failures a new integrator
# hits, so they cannot be the ones that break the contract.


def _routing_app() -> TestClient:
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from agentclaw.community.adapters.http.openapi_v1.responses import (
        is_public_api,
        unmapped_error_response,
    )

    app = FastAPI()

    # Same registration the real app performs (adapters/http/app.py).
    @app.exception_handler(StarletteHTTPException)
    async def _handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if is_public_api(request):
            return unmapped_error_response(
                exc.status_code, request, headers=exc.headers
            )
        return await http_exception_handler(request, exc)

    @app.get(f"{PUBLIC_API_PREFIX}/bots/known")
    async def _public():  # pragma: no cover - only its absence/method is tested
        return {}

    @app.get("/api/bots/known")
    async def _internal():  # pragma: no cover
        return {}

    return TestClient(app)


def _assert_envelope(resp, status: int):
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == status * 1000
    assert body["data"] is None


def test_public_unknown_path_is_enveloped():
    _assert_envelope(_routing_app().get(f"{PUBLIC_API_PREFIX}/bots/nope"), 404)


def test_public_wrong_method_is_enveloped():
    _assert_envelope(_routing_app().post(f"{PUBLIC_API_PREFIX}/bots/known"), 405)


def test_internal_routing_errors_keep_detail_shape():
    """Scoping guard: existing internal clients must be unaffected."""
    resp = _routing_app().post("/api/bots/known")
    assert resp.status_code == 405
    assert "detail" in resp.json()


# ----- R7/F33: protocol headers survive the envelope ------------------------


def test_wrong_method_keeps_the_allow_header():
    """A 405 without ``Allow`` says "wrong" without saying what would be right.

    Starlette attaches the permitted methods to the exception; rebuilding the
    response from the status code alone silently dropped them.
    """
    resp = _routing_app().post(f"{PUBLIC_API_PREFIX}/bots/known")
    assert resp.status_code == 405
    assert "GET" in resp.headers.get("allow", "")
    # …and it is still an envelope, not the default detail body.
    assert resp.json()["code"] == 405000


def test_error_envelope_carries_the_trace_header():
    """``X-Trace-ID`` mirrors ``request_id`` on failures too, not just success."""
    from unittest.mock import patch

    with patch(
        "agentclaw.community.adapters.http.openapi_v1.responses._trace_id",
        return_value="trace-123",
    ):
        resp = _routing_app().get(f"{PUBLIC_API_PREFIX}/bots/nope")
    assert resp.headers["x-trace-id"] == "trace-123"
    assert resp.json()["request_id"] == "trace-123"


def test_body_describing_headers_are_not_forwarded():
    """A stale Content-Length would describe the body we discarded."""
    from fastapi import HTTPException

    from agentclaw.community.adapters.http.openapi_v1.responses import (
        unmapped_error_response,
    )

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _handler(request: Request, exc: HTTPException) -> JSONResponse:
        return unmapped_error_response(exc.status_code, request, headers=exc.headers)

    @app.get(f"{PUBLIC_API_PREFIX}/bots/boom")
    async def _public():
        raise HTTPException(
            status_code=409,
            headers={"Content-Length": "99999", "X-Keep-Me": "yes"},
        )

    resp = TestClient(app).get(f"{PUBLIC_API_PREFIX}/bots/boom")
    assert resp.status_code == 409
    assert resp.headers["x-keep-me"] == "yes"          # protocol header kept
    assert resp.headers["content-length"] != "99999"   # body header recomputed
    assert resp.json()["code"] == 409000               # body actually parses
