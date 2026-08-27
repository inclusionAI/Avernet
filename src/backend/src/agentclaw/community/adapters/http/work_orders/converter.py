"""Shared HTTP translation for the unified work-order event operation.

Both the gateway-facing OpenAPI route and the authenticated internal HTTP route
use this adapter so request-to-service and service-to-response mapping cannot
drift.  Domain policy remains in ``WorkOrderServiceProtocol``.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.work_orders.schemas import (
    CreateWorkOrderEventRequest,
    WorkOrderEventCreated,
    WorkOrderEventStatus,
)
from agentclaw.community.api.work_order_service import WorkOrderServiceProtocol
from agentclaw.community.core.work_orders.models import (
    NotificationCategory as DomainNotificationCategory,
)


def create_work_order_event_data(
    *,
    body: CreateWorkOrderEventRequest,
    actor_id: str,
    service: WorkOrderServiceProtocol,
) -> WorkOrderEventCreated:
    """Delegate one event creation and translate its result to the HTTP DTO."""
    result = service.create_work_order_event(
        event_category=DomainNotificationCategory(body.event_category),
        biz_type=body.biz_type,
        biz_id=body.biz_id,
        event_type=body.event_type,
        applicant_user_id=body.applicant_user_id,
        approver_user_ids=body.approver_user_ids,
        recipient_user_ids=body.recipient_user_ids,
        title=body.title,
        content=body.content,
        apply_reason=body.apply_reason,
        biz_data=body.biz_data,
        actor_id=actor_id,
    )
    return WorkOrderEventCreated(
        event_category=result.event_category,
        work_order_id=result.work_order_id,
        work_order_no=result.work_order_no,
        notification_ids=result.notification_ids,
        status=WorkOrderEventStatus(result.status),
    )
