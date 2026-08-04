"""Where the auth seam's 401 handler is registered, and why that matters.

``MissingPrincipalError`` and ``PrincipalVerificationError`` are raised inside
``require_principal`` — a FastAPI **dependency**, so they surface in
``solve_dependencies`` before the route handler runs. That placement is what
these tests are about.

Starlette splits registered exception handlers by key. A handler registered for
``Exception`` becomes ``ServerErrorMiddleware``'s handler, and that middleware
sends the response and then unconditionally re-raises so the server can log a
crash ("We always continue to raise the exception",
``starlette/middleware/errors.py``). A correct 401 therefore went out on the
wire *and* uvicorn logged a full ASGI traceback behind it — around a hundred
lines per request, on the one path that takes every request when auth is
misconfigured.

Registering the concrete types puts them in the inner ``ExceptionMiddleware``,
which answers and does not re-raise. The tests below pin both halves: the fix
works, and the control case shows the re-raise is real rather than folklore. If
Starlette ever stops re-raising, ``test_the_catch_all_alone_re_raises`` fails
and the extra registration can be reconsidered.

The app is driven through the raw ASGI interface rather than ``TestClient``,
because that is the only way to observe what uvicorn observes: ``TestClient``
swallows the re-raise (``raise_server_exceptions=False``) or converts it into a
test failure (the default), and neither distinguishes the two wirings.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.core.gateway_principal import PrincipalVerificationError

pytestmark = pytest.mark.asyncio

PUBLIC_PATH = f"{PUBLIC_API_PREFIX}/bots"
INTERNAL_PATH = "/api/bots"


def _build_app(*, register_concrete: bool, raised: Exception, path: str) -> FastAPI:
    """Mirror the prod wiring, importing the real handlers from ``app.py``."""
    from agentclaw.community.adapters.http.app import (
        _principal_error_handler,
        _unhandled_exception_handler,
    )

    app = FastAPI()
    # The prod stack wraps the router in several BaseHTTPMiddleware layers
    # (tracer, request logging). They re-raise app exceptions on the way out,
    # and they are in the traceback this file exists to remove, so the shape is
    # reproduced rather than simplified away.
    app.add_middleware(BaseHTTPMiddleware, dispatch=lambda r, call_next: call_next(r))

    async def require_principal() -> None:
        raise raised

    @app.get(path, dependencies=[Depends(require_principal)])
    async def route() -> dict:
        return {"unreachable": True}

    app.add_exception_handler(Exception, _unhandled_exception_handler)
    if register_concrete:
        app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
        app.add_exception_handler(
            PrincipalVerificationError, _principal_error_handler
        )
    return app


async def _call_asgi(app: FastAPI, path: str) -> tuple[int | None, str | None]:
    """Drive ``app`` as uvicorn does; return the wire status and any re-raise."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("11.232.198.238", 0),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "app": app,
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    reraised: str | None = None
    try:
        await app(scope, receive, send)
    except Exception as exc:  # noqa: BLE001 — the observation under test
        reraised = type(exc).__name__

    status = next(
        (m["status"] for m in sent if m["type"] == "http.response.start"), None
    )
    return status, reraised


@pytest.mark.parametrize(
    "error",
    [
        MissingPrincipalError("no verified caller for this request"),
        PrincipalVerificationError("principal token rejected: Signature ..."),
    ],
    ids=["missing-principal", "verification-failed"],
)
async def test_an_unverifiable_caller_gets_a_401_and_nothing_else(error: Exception):
    """The whole point: a 401 on the wire, and no crash reported to the server."""
    app = _build_app(register_concrete=True, raised=error, path=PUBLIC_PATH)

    status, reraised = await _call_asgi(app, PUBLIC_PATH)

    assert status == 401
    assert reraised is None, (
        "re-raised to the ASGI server, which logs it as 'Exception in ASGI "
        "application' — a ~100-line traceback behind an already-sent 401"
    )


async def test_the_catch_all_alone_re_raises():
    """The control, and the reason the concrete registration exists.

    Not a test of our code but of Starlette's: it documents the behavior being
    worked around, so that if Starlette stops re-raising this fails loudly and
    the workaround can be dropped rather than cargo-culted.
    """
    app = _build_app(
        register_concrete=False,
        raised=MissingPrincipalError("no verified caller for this request"),
        path=PUBLIC_PATH,
    )

    status, reraised = await _call_asgi(app, PUBLIC_PATH)

    assert status == 401, "the catch-all does produce the right status..."
    assert reraised == "MissingPrincipalError", "...and then re-raises anyway"


async def test_the_public_surface_keeps_its_envelope():
    """Routing around the catch-all must not route around the contract.

    The status and body still come from ``ENVELOPE_ERRORS``, so this handler
    adds a way out of the middleware stack, not a second opinion on the answer.
    """
    from agentclaw.community.adapters.http.app import _principal_error_handler

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": PUBLIC_PATH,
            "headers": [],
            "query_string": b"",
        }
    )

    response = await _principal_error_handler(
        request, MissingPrincipalError("no verified caller for this request")
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert b'"code":401000' in response.body
    assert b'"message":"Unauthorized"' in response.body


async def test_an_internal_route_keeps_the_detail_shape():
    """``/api`` clients parse ``{"detail": ...}`` and never learn the Envelope."""
    from agentclaw.community.adapters.http.app import _principal_error_handler

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": INTERNAL_PATH,
            "headers": [],
            "query_string": b"",
        }
    )

    response = await _principal_error_handler(
        request, MissingPrincipalError("no verified caller for this request")
    )

    assert response.status_code == 401
    assert response.body == b'{"detail":"Unauthorized"}'


async def test_the_reason_is_logged_but_never_returned(caplog):
    """The diagnosis goes to the operator; the caller gets one fixed answer.

    The verifier's message names the token's ``kid`` and the fingerprint of the
    key it was judged against. Returning any of that would tell a forger which
    part of a token to fix, so the split has to hold in both directions.
    """
    import logging

    from agentclaw.community.adapters.http.app import _principal_error_handler

    diagnosis = (
        "principal token rejected: Signature verification failed "
        "[token alg='HS256' kid='bare'; verifier key fp=70b333b7, "
        "expects aud='backend' iss='gateway']"
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": PUBLIC_PATH,
            "headers": [],
            "query_string": b"",
        }
    )

    with caplog.at_level(logging.WARNING):
        response = await _principal_error_handler(
            request, PrincipalVerificationError(diagnosis)
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "fp=70b333b7" in logged, "the operator gets the diagnosis"
    assert "[Public 401]" in logged, "and the existing grep still finds the line"
    assert b"fp=" not in response.body, "the caller does not"
    assert b"kid" not in response.body
