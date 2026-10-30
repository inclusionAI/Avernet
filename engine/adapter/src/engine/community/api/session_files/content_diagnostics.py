"""Safe HTTP content-boundary diagnostics, including terminal stream outcomes."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("engine.session_files.api")


async def logged_content_response(
    action: Callable[[], Awaitable[Response]],
    *,
    session_key: str,
    resource_id: str,
    disposition: str,
) -> Response:
    started = time.monotonic()
    size_bytes = None

    def outcome(event: str, status: str | int, error_type: str = "") -> None:
        # COSEC: allowlist business metadata; never log bodies, headers, signed
        # URLs or exception text (which can contain credentials from dependencies).
        fields = {
            "system": "engine", "operation": "session_files.content",
            "method": "GET", "route": "/api/session-files/{resource_id}/content",
            "sessionKey": session_key, "resource_id": resource_id,
            "disposition": disposition, "event": event, "status": status,
            "size_bytes": size_bytes, "error_type": error_type,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
        log.info(
            "engine.session_files.api.content.%s system=engine method=GET "
            "route=/api/session-files/{resource_id}/content sessionKey=%r "
            "resource_id=%s disposition=%s status=%s size_bytes=%s elapsed_ms=%s error_type=%s",
            event, session_key, resource_id, disposition, status, size_bytes,
            fields["elapsed_ms"], error_type, extra=fields,
        )

    outcome("request", "request")
    try:
        response = await action()
    except HTTPException as exc:
        outcome("failure", exc.status_code, type(exc).__name__)
        raise
    except BaseException as exc:
        outcome("failure", 500, type(exc).__name__)
        raise

    if isinstance(response, StreamingResponse):
        size_bytes = int(response.headers["content-length"])
        original_body = response.body_iterator

        async def body():
            try:
                async for chunk in original_body:
                    yield chunk
            except BaseException as exc:
                outcome("failure", response.status_code, type(exc).__name__)
                raise
            else:
                outcome("success", response.status_code)

        response.body_iterator = body()
    else:
        if isinstance(response, JSONResponse):
            size_bytes = json.loads(response.body).get("size_bytes")
        outcome("success", response.status_code)
    return response
