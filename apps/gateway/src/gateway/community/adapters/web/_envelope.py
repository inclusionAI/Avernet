"""The standard response envelope for failures the gateway produces itself.

The upstream (the backend) writes the envelope for everything it answers, and
the gateway relays that verbatim. What reaches a caller *without* passing
through an upstream — an unknown route, a refused credential, an unreachable
upstream, a malformed admin request, an escaped exception — is the gateway's to
answer, and it answers in the same shape the OpenAPI contract promises::

    {"code": <HTTP status * 1000 + subcode>, "message": str, "data": null,
     "request_id": <trace id>}

``request_id`` is never empty. :class:`RequestIdMiddleware` fixes it once per
request, when the request starts: the OpenTelemetry trace id, so it matches the
gateway's log lines, or a generated id when no span is recording. It is read
from the request rather than from the live span because the outermost 500
handler runs after the tracing middleware has already closed its span.

Gateway refusals with a known cause carry their own subcode (``401001`` for a
failed authentication, ``502001`` for an unreachable upstream). Failures the
framework raises — routing 404/405, request validation, a bare
``HTTPException`` — have no business cause to name, so they take subcode 0,
matching the backend's rule for unmapped errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.community.logger import get_logger
from gateway.community.tracer import get_tracer_plugin

logger = get_logger("envelope")

# Headers the response writes itself; an exception's copy would contradict it.
_OWNED_HEADERS = frozenset({"content-length", "content-type", "transfer-encoding"})


class GatewayError(Exception):
    """A gateway refusal raised where a response cannot be returned directly
    (a FastAPI dependency), answered as an envelope by the installed handler."""

    def __init__(self, status: int, subcode: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.subcode = subcode
        self.message = message


_STATE_KEY = "request_id"


def _new_request_id() -> str:
    """The active span's trace id, or a fresh id when no span is recording.

    The bare tracer answers ``"-"`` without a valid span; that placeholder is
    not an id a caller can quote back, so it is replaced like a failure is.
    """
    try:
        trace_id = str(get_tracer_plugin().get_trace_id())
    except Exception:
        trace_id = ""
    return trace_id if trace_id.strip("-") else uuid4().hex


class RequestIdMiddleware:
    """Fix the request's id on ``scope["state"]`` while its span is active.

    Must sit *inside* the tracing middleware, so the span exists when it runs.
    ``scope["state"]`` is one dict shared by every layer of the request, so the
    outermost 500 handler reads the same id after the span has closed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            state.setdefault(_STATE_KEY, _new_request_id())
        await self.app(scope, receive, send)


def request_id(connection: HTTPConnection) -> str:
    """This request's id; generated here if no middleware fixed one first."""
    state = connection.state
    value = getattr(state, _STATE_KEY, None)
    if not value:
        value = _new_request_id()
        setattr(state, _STATE_KEY, value)
    return str(value)


def error_response(
    request: HTTPConnection,
    status: int,
    message: str,
    *,
    subcode: int = 0,
    data: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """A gateway-generated error in the standard envelope shape."""
    return JSONResponse(
        status_code=status,
        content={
            "code": status * 1000 + subcode,
            "message": message,
            "data": data,
            "request_id": request_id(request),
        },
        headers={
            k: v for k, v in (headers or {}).items() if k.lower() not in _OWNED_HEADERS
        }
        or None,
    )


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


async def _gateway_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GatewayError)
    return error_response(request, exc.status, exc.message, subcode=exc.subcode)


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Routing 404/405 and any ``HTTPException`` a gateway-local route raises.

    The detail is the gateway's own wording (``dev_login``'s "next must be a
    port number"), so it is kept as the message; Starlette fills it with the
    status phrase when the raise site gave none. ``exc.headers`` is forwarded because it carries the
    actionable part of some statuses — a 405's ``Allow`` list.
    """
    assert isinstance(exc, StarletteHTTPException)
    return error_response(
        request,
        exc.status_code,
        exc.detail or _phrase(exc.status_code),
        headers=exc.headers,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """A malformed request to a gateway-local route, as a 422 envelope.

    FastAPI's default body quotes each error's ``input`` — on the admin routes
    that can be a caller's plaintext credential — so the caller gets only
    "Invalid request", and which field failed is logged by ``loc``/``type``/
    ``msg`` alone. No ``exc_info``: the traceback would print the input too.
    """
    assert isinstance(exc, RequestValidationError)
    logger.warning(
        "validation failed on %s %s: %s",
        request.method,
        request.url.path,
        [
            {"loc": e.get("loc"), "type": e.get("type"), "msg": e.get("msg")}
            for e in exc.errors()
        ],
    )
    return error_response(request, 422, "Invalid request")


def install_error_envelope(app: FastAPI) -> None:
    """Answer the gateway's own framework-level failures with the envelope.

    Call it *before* the tracer installs its middleware: a middleware added
    later wraps the ones added earlier, so this keeps :class:`RequestIdMiddleware`
    inside the span.
    """
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(GatewayError, _gateway_error)
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)


def server_error_response(request: HTTPConnection) -> JSONResponse:
    """The envelope for an exception nothing else handled."""
    return error_response(request, 500, _phrase(500))
