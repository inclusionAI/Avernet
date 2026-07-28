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
from agentclaw.community.adapters.http.openapi_v1.responses import error_response


class _Body(BaseModel):
    required_field: str


def _app() -> TestClient:
    app = FastAPI()

    # Same registration the real app performs (adapters/http/app.py).
    @app.exception_handler(RequestValidationError)
    async def _handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith(PUBLIC_API_PREFIX):
            return error_response(422, "Invalid request", request)
        return await request_validation_exception_handler(request, exc)

    @app.post(f"{PUBLIC_API_PREFIX}/bots")
    async def _public(body: _Body):  # pragma: no cover - never reached
        return {}

    @app.post("/api/bots")
    async def _internal(body: _Body):  # pragma: no cover - never reached
        return {}

    return TestClient(app)


def test_public_validation_error_is_enveloped():
    resp = _app().post(f"{PUBLIC_API_PREFIX}/bots", json={})
    assert resp.status_code == 422
    body = resp.json()
    # The uniform envelope, not FastAPI's {"detail": [...]}.
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == 422000
    assert body["data"] is None


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
