"""Relay Session REST API routes.

Exposes relay session data to the agentclawproxy relay server via HTTP.
The proxy uses PUT to transition sessions (init -> active -> closed) and
GET to query current routing state.

Endpoints:
- GET /api/v1/paas/relay-sessions/{session_id} - Query relay session routing info
- PUT /api/v1/paas/relay-sessions/{session_id} - Update relay session status
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.ws_relay_session import (
        WsRelaySessionRepository,
    )

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/paas", tags=["Relay Session 管理(内部)"])


class UpdateRelaySessionRequest(BaseModel):
    """Request body for updating relay session status."""

    status: Literal["active", "closed"] = Field(
        ..., description="Target relay session status"
    )
    connected_server_instance: str | None = Field(
        default=None, description="Server instance ID (required for active)"
    )
    connected_route_info: dict[str, Any] | None = Field(
        default=None, description="Route info dict (required for active)"
    )


class RelaySessionResponse(BaseModel):
    """Response model for relay session endpoints."""

    session_id: str
    status: str


@router.get("/relay-sessions/{session_id}")
@inject
async def get_relay_session(
    session_id: str,
    repo: WsRelaySessionRepository = Depends(
        Provide[ApplicationContainer.repository.ws_relay_session_repository]
    ),
):
    """Query relay session routing info by session_id."""
    logger.info("GET relay-sessions/%s", session_id)

    record = repo.get_by_session_id(session_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "RELAY_SESSION_NOT_FOUND",
                "message": f"Relay session {session_id} not found",
            },
        )

    return {
        "id": record.id,
        "gmt_create": record.gmt_create.isoformat() if record.gmt_create else None,
        "gmt_modified": record.gmt_modified.isoformat()
        if record.gmt_modified
        else None,
        "session_id": record.session_id,
        "machine_id": record.machine_id,
        "connected_server_instance": record.connected_server_instance,
        "status": record.status,
        "env": record.env,
        "gmt_close": record.gmt_close.isoformat() if record.gmt_close else None,
        "connected_route_info": record.connected_route_info,
        "operator": record.operator,
    }


@router.put("/relay-sessions/{session_id}")
@inject
async def update_relay_session(
    session_id: str,
    body: UpdateRelaySessionRequest,
    repo: WsRelaySessionRepository = Depends(
        Provide[ApplicationContainer.repository.ws_relay_session_repository]
    ),
):
    """Update relay session status (active/close).

    State transitions per D-05:
    - init/active -> active (or same status, idempotent) → 200
    - init/active -> closed → 200
    - closed -> active → 409 (reverse transition)
    - Non-existent session → 404
    """
    logger.info("PUT relay-sessions/%s status=%s", session_id, body.status)

    record = repo.get_by_session_id(session_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "RELAY_SESSION_NOT_FOUND",
                "message": f"Relay session {session_id} not found",
            },
        )

    target_status = body.status

    # Idempotency: same status is a no-op (D-05)
    if record.status == target_status:
        logger.info(
            "PUT relay-sessions/%s: idempotent no-op (already %s)",
            session_id,
            target_status,
        )
        return {"session_id": session_id, "status": record.status}

    # State transition validation
    # _validate_transition raises DeviceCreationError on illegal transition,
    # which is caught by the existing device_creation_exception_handler in app.py
    repo._validate_transition(record.status, target_status)

    if target_status == "active":
        server_instance = body.connected_server_instance or ""
        route_info = body.connected_route_info or {}

        # Validate: active status requires routing information
        if not server_instance.strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "INVALID_PARAMS",
                    "message": "connected_server_instance is required for active status",
                },
            )
        if not route_info:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "INVALID_PARAMS",
                    "message": "connected_route_info is required for active status",
                },
            )

        repo.update_active(
            session_id=session_id,
            connected_server_instance=server_instance,
            connected_route_info=route_info,
        )
    elif target_status == "closed":
        repo.update_closed(session_id=session_id)

    logger.info(
        "PUT relay-sessions/%s: transition %s -> %s done",
        session_id,
        record.status,
        target_status,
    )
    return {"session_id": session_id, "status": target_status}
