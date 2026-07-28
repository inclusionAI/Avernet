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
