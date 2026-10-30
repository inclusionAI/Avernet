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
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.api.paas import WsRelaySessionService

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
    service: WsRelaySessionService = Depends(
        Provide[ApplicationContainer.services.ws_relay_session_service]
    ),
):
    """Query relay session routing info by session_id."""
    logger.info("GET relay-sessions/%s", session_id)

    record = service.get_by_session_id(session_id)

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
    service: WsRelaySessionService = Depends(
        Provide[ApplicationContainer.services.ws_relay_session_service]
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

    status = service.update_status(
        session_id=session_id,
        status=body.status,
        connected_server_instance=body.connected_server_instance,
        connected_route_info=body.connected_route_info,
    )
    return {"session_id": session_id, "status": status}
