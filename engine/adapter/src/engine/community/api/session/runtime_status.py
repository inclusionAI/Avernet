"""Read-only runtime observation across the WebSocket and A2A run registries.

Device HTTP ingress uses the same authenticated proxy as session history. In
addition, check upstream history access with the supplied MCP identity before
exposing process-local activity. This is not an execution or resume endpoint.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from engine.community.api.response import ApiResponse
from engine.community.api.transport.a2a import router as a2a_router
from engine.community.api.transport.ws_server import get_server
from engine.community.config import load_mcp_token_settings
from engine.community.core.engine.context import AuthContext
from engine.community.manager import EngineManager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/runtime/status")
async def get_session_runtime_status(
    request: Request,
    session_id: str = Query(min_length=1),
) -> ApiResponse:
    manager = EngineManager.get_instance()
    if manager.engine != "openclaw":
        return ApiResponse(
            success=True,
            data={
                "session_id": session_id,
                "found": False,
                "reason": "UNSUPPORTED_ENGINE",
            },
        )
    relay = manager.relay
    if relay is None:
        raise HTTPException(status_code=503, detail="Session gateway unavailable")
    token = request.headers.get(load_mcp_token_settings().header_name)
    try:
        access = await relay.forward_request(
            request_id=f"runtime-status-{uuid.uuid4().hex}",
            method="chat.history",
            params={"sessionKey": session_id, "limit": 1},
            auth=AuthContext(token=token),
            timeout=10.0,
        )
    except (ConnectionError, TimeoutError) as exc:
        raise HTTPException(
            status_code=503, detail="Session gateway unavailable"
        ) from exc
    if not access.ok:
        code = access.error.code if access.error else "UNAVAILABLE"
        status = 403 if code in {"FORBIDDEN", "UNAUTHORIZED", "AUTH_REQUIRED"} else 503
        raise HTTPException(
            status_code=status, detail="Session history access unavailable"
        )
    records = [
        get_server().get_session_runtime_status(session_id),
        a2a_router.get_session_runtime_status(session_id),
    ]
    if any(record.get("reason") == "AMBIGUOUS_RUN" for record in records):
        return ApiResponse(success=True, data={
            "session_id": session_id, "found": False, "reason": "AMBIGUOUS_RUN"})
    found = [record for record in records if record["found"]]
    active = [record for record in found if record["state"] in {"starting", "running"}]
    if len(active) > 1:
        return ApiResponse(success=True, data={
            "session_id": session_id, "found": False, "reason": "AMBIGUOUS_RUN"})
    # Prefer the active execution to retained terminal records. With only
    # terminal observations, use the most recently started observed run.
    data = active[0] if active else max(
        found, key=lambda record: record["started_at"], default=records[0])
    return ApiResponse(success=True, data=data)
