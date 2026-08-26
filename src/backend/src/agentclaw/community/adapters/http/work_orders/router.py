"""Authenticated internal HTTP mirror of unified work-order event creation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.work_orders.schemas import (
    CreateWorkOrderEventRequest,
    WorkOrderEventCreated,
)
from agentclaw.community.adapters.http.work_orders.event_adapter import (
    create_work_order_event_data,
)
from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/v1/work-orders", tags=["work-orders"])


@router.post(
    "/events",
    status_code=201,
    response_model=Envelope[WorkOrderEventCreated],
)
@envelope_errors
async def create_work_order_event_http(
    body: CreateWorkOrderEventRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderEventCreated]:
    """Create an approval or notice event for the authenticated user."""
    data = create_work_order_event_data(
        body=body,
        actor_id=user.staffId,
        service=service,
    )
    return created(data, request)
