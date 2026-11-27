"""Low-level public-envelope construction shared by routes and error mappers."""

from __future__ import annotations

from http import HTTPStatus
from typing import Mapping

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import ErrorEnvelope


def trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "") or ""


def is_public_api(request: Request) -> bool:
    from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX

    return request.url.path.startswith(PUBLIC_API_PREFIX)


def unmapped_error_response(
    http_status: int,
    request: Request,
    *,
    headers: Mapping[str, str] | None = None,
    detail: str | None = None,
) -> JSONResponse:
    """The envelope for a failure with no mapped error type.

    ``detail`` — the reason the handler raised with — is used when present. A
    bare reason phrase cannot distinguish two 409s (a version that moved on vs
    a name already taken), and the caller has no other channel to learn which
    one it hit.
    """
    try:
        message = HTTPStatus(http_status).phrase
    except ValueError:
        message = "Error"
    if detail and detail.strip():
        message = detail.strip()
    return error_response_for(http_status, message, request, headers=headers)


def error_response(http_status: int, message: str, request: Request) -> JSONResponse:
    return error_response_for(http_status, message, request)


def error_response_for(
    http_status: int,
    message: str,
    request: Request,
    *,
    headers: Mapping[str, str] | None = None,
    code: int | None = None,
    data: object | None = None,
) -> JSONResponse:
    resolved_code = code if code is not None else http_status * 1000
    request_id = trace_id(request)
    if data is None:
        content = ErrorEnvelope(
            code=resolved_code, message=message, data=None, request_id=request_id
        ).model_dump()
    else:
        content = {
            "code": resolved_code,
            "message": message,
            "data": jsonable_encoder(data),
            "request_id": request_id,
        }
    response_headers = {
        key: value
        for key, value in (headers or {}).items()
        if key.lower() not in {"content-length", "content-type", "transfer-encoding"}
    }
    if request_id:
        response_headers.setdefault("X-Trace-ID", request_id)
    return JSONResponse(
        status_code=http_status,
        content=content,
        headers=response_headers,
    )
