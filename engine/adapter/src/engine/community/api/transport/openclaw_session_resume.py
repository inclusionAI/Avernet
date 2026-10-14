"""Session-key recovery, authorized through the existing history read path."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from engine.community.api.transport.openclaw_resume import OpenClawResumeRegistry
from engine.community.kernel.frames import ErrorShape, RequestFrame, ResponseFrame


async def resume_session(
    registry: OpenClawResumeRegistry,
    websocket: Any,
    conn_id: str,
    request: RequestFrame,
    read_history: Callable[[RequestFrame], Awaitable[ResponseFrame]],
) -> ResponseFrame:
    session_key = (request.params or {}).get("sessionKey")
    if not isinstance(session_key, str) or not session_key.strip():
        return ResponseFrame.err_response(
            request.id, ErrorShape("INVALID_REQUEST", "Missing sessionKey")
        )
    # COSEC: a session key is not a credential. Use the same authenticated
    # upstream read path as chat.history before exposing in-memory output.
    access = await read_history(RequestFrame(
        id=request.id, method="chat.history",
        params={"sessionKey": session_key, "limit": 1},
    ))
    if not access.ok:
        return access
    try:
        run = registry.lookup_session(session_key)
    except ValueError:
        return ResponseFrame.err_response(
            request.id, ErrorShape("AMBIGUOUS_RUN", "Multiple active runs for this session")
        )
    if run is None:
        return ResponseFrame.ok_response(
            request.id, {"resumed": False, "reason": "NO_ACTIVE_RUN"}
        )
    result = await run.attach(conn_id, websocket)
    if result is None:
        return ResponseFrame.ok_response(
            request.id, {"resumed": False, "reason": run.reason or "UNAVAILABLE"}
        )
    return ResponseFrame.ok_response(request.id, {"resumed": True, **result})
