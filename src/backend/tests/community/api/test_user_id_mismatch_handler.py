"""Where the explicit user id's 403 handler is registered, and why that matters.

``UserIdMismatchError`` is raised inside ``require_user_id`` — a FastAPI
**dependency**, so it surfaces in ``solve_dependencies`` before the route
handler runs and ``@envelope_errors`` never sees it. That is the same placement
``MissingPrincipalError`` has, and it needs the same treatment for the same
reason: a handler registered only for ``Exception`` becomes
``ServerErrorMiddleware``'s, and that middleware sends the response and then
unconditionally re-raises so the server can log a crash. The caller would get a
correct 403 followed by a ~100-line ASGI traceback in the server log, on a path
a misconfigured partner integration takes on *every* request.

The full argument, and the control test proving Starlette really does re-raise,
live in ``test_principal_error_handler.py``. This file pins the same wiring for
the 403 and the thing that is specific to it: the response must say nothing
about which user was named.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX
from agentclaw.community.adapters.http.openapi_v1.errors import UserIdMismatchError

pytestmark = pytest.mark.asyncio

PUBLIC_PATH = f"{PUBLIC_API_PREFIX}/bots"
INTERNAL_PATH = "/api/bots"

_RAISED = UserIdMismatchError("request user id is not the verified caller")


def _build_app(*, register_concrete: bool) -> FastAPI:
    """Mirror the prod wiring, importing the real handlers from ``app.py``."""
    from agentclaw.community.adapters.http.app import (
        _unhandled_exception_handler,
        _user_id_mismatch_handler,
    )

    app = FastAPI()
    # The prod stack wraps the router in several BaseHTTPMiddleware layers; they
    # re-raise app exceptions on the way out and are in the traceback this file
    # exists to remove, so the shape is reproduced rather than simplified away.
    app.add_middleware(BaseHTTPMiddleware, dispatch=lambda r, call_next: call_next(r))

    async def require_user_id() -> None:
        raise _RAISED

    @app.get(PUBLIC_PATH, dependencies=[Depends(require_user_id)])
    async def route() -> dict:
        return {"unreachable": True}

    app.add_exception_handler(Exception, _unhandled_exception_handler)
    if register_concrete:
        app.add_exception_handler(UserIdMismatchError, _user_id_mismatch_handler)
    return app


async def _call_asgi(app: FastAPI) -> tuple[int | None, str | None]:
    """Drive ``app`` as uvicorn does; return the wire status and any re-raise."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": PUBLIC_PATH,
        "raw_path": PUBLIC_PATH.encode(),
        "query_string": b"user_id=someone-else",
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


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"user_id=someone-else",
        }
    )


async def test_naming_another_user_gets_a_403_and_nothing_else():
    """The whole point: a 403 on the wire, and no crash reported to the server."""
    status, reraised = await _call_asgi(_build_app(register_concrete=True))

    assert status == 403
    assert reraised is None, (
        "re-raised to the ASGI server, which logs it as 'Exception in ASGI "
        "application' — a ~100-line traceback behind an already-sent 403"
    )


async def test_the_catch_all_alone_re_raises():
    """The control, and the reason the concrete registration exists.

    Mirrors ``test_principal_error_handler.py``: if Starlette ever stops
    re-raising, this fails and the extra registration can be reconsidered.
    """
    status, reraised = await _call_asgi(_build_app(register_concrete=False))

    assert status == 403, "the catch-all does produce the right status..."
    assert reraised == "UserIdMismatchError", "...and then re-raises anyway"


async def test_the_public_surface_keeps_its_envelope():
    """The status and body come from ``ENVELOPE_ERRORS``, not from this handler."""
    from agentclaw.community.adapters.http.app import _user_id_mismatch_handler

    response = await _user_id_mismatch_handler(_request(PUBLIC_PATH), _RAISED)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 403
    assert b'"code":403000' in response.body
    assert b'"message":"Forbidden"' in response.body
    assert b'"data":null' in response.body


async def test_an_internal_route_keeps_the_detail_shape():
    """``/api`` clients parse ``{"detail": ...}`` and never learn the Envelope.

    Unreachable while the dependency is mounted only on the public surface —
    kept so a future internal caller of the same seam is answered in the shape
    its clients already parse.
    """
    from agentclaw.community.adapters.http.app import _user_id_mismatch_handler

    response = await _user_id_mismatch_handler(_request(INTERNAL_PATH), _RAISED)

    assert response.status_code == 403
    assert response.body == b'{"detail":"Forbidden"}'


async def test_the_response_names_no_user(caplog):
    """The refusal must not confirm or deny anything about the id it was given.

    A 403 that echoed the requested user would turn this endpoint into an
    existence oracle for other people's accounts. Which ids disagreed is logged
    by ``require_user_id``; the response carries one fixed word.
    """
    from agentclaw.community.adapters.http.app import _user_id_mismatch_handler

    with caplog.at_level(logging.WARNING):
        response = await _user_id_mismatch_handler(_request(PUBLIC_PATH), _RAISED)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "[Public 403]" in logged, "the operator gets a greppable line"
    assert b"someone-else" not in response.body
    assert b"user" not in response.body.lower().replace(b'"data":null', b"")
