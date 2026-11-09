"""The standard response envelope for failures the gateway produces itself.

The upstream (the backend) writes the envelope for everything it answers, and
the gateway relays that verbatim. What reaches a caller *without* passing
through an upstream — an unknown route, a refused credential, an unreachable
upstream, a malformed admin request, an escaped exception — is the gateway's to
answer, and it answers in the same shape the OpenAPI contract promises::

    {"code": <HTTP status * 1000 + subcode>, "message": str, "data": null,
     "request_id": <trace id>}

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

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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


def request_id() -> str:
    """The current trace id, or ``""`` when no span is active."""
    try:
        return str(get_tracer_plugin().get_trace_id())
    except Exception:
        return ""


def error_response(
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
            "request_id": request_id(),
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
    return error_response(exc.status, exc.message, subcode=exc.subcode)


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Routing 404/405 and any ``HTTPException`` a gateway-local route raises.

    The detail is the gateway's own wording (``dev_login``'s "next must be a
    port number"), so it is kept as the message; Starlette fills it with the
    status phrase when the raise site gave none. ``exc.headers`` is forwarded because it carries the
    actionable part of some statuses — a 405's ``Allow`` list.
    """
    assert isinstance(exc, StarletteHTTPException)
    return error_response(
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
    return error_response(422, "Invalid request")


def install_error_envelope(app: FastAPI) -> None:
    """Answer the gateway's own framework-level failures with the envelope."""
    app.add_exception_handler(GatewayError, _gateway_error)
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)


def server_error_response() -> JSONResponse:
    """The envelope for an exception nothing else handled."""
    return error_response(500, _phrase(500))
