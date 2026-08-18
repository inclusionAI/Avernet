"""OpenAPI v1 adapter for work orders and recipient notifications."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.openapi_v1.work_orders.schemas import (
    CreateSpaceJoinRequest,
    NotificationDetailResponse,
    NotificationReadResponse,
    NotificationsReadAllResponse,
    SpaceJoinRequestCreated,
    UnreadCountResponse,
    WorkOrderDetailContent,
    WorkOrderDetailResponse,
    WorkOrderListItem,
    WorkOrderReviewRequest,
    WorkOrderReviewResponse,
)
from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderItemType,
    WorkOrderListItem as DomainListItem,
    WorkOrderQueryType,
)
from agentclaw.community.di import Injected


router = APIRouter(tags=["work-orders"])
PrincipalDep = Annotated[Principal, Depends(require_principal)]
PositiveIdPath = Annotated[int, Path(ge=1)]
PageNoQuery = Annotated[int, Query(ge=1)]
PageSizeQuery = Annotated[int, Query(ge=1, le=100)]


def _list_item(item: DomainListItem) -> WorkOrderListItem:
    work_order = item.work_order
    notification = item.notification
    modified = (
        notification.gmt_modified
        if notification is not None
        else work_order.gmt_modified
    )
    category = notification.notification_category if notification is not None else None
    item_type = (
        WorkOrderItemType(category.value)
        if category is not None
        else WorkOrderItemType.APPROVAL
    )
    return WorkOrderListItem(
        item_id=(
            f"NOTIFICATION_{notification.id}"
            if notification is not None
            else f"WORK_ORDER_{work_order.id}"
        ),
        item_type=item_type,
        work_order_id=work_order.id,
        work_order_no=work_order.work_order_no,
        notification_id=notification.id if notification is not None else None,
        notification_category=category,
        biz_type=work_order.biz_type,
        biz_id=work_order.biz_id,
        applicant_user_id=work_order.applicant_user_id,
        apply_reason=work_order.apply_reason,
        reviewer_user_id=work_order.reviewer_user_id,
        review_remark=work_order.review_remark,
        reviewed_at=work_order.reviewed_at,
        recipient_user_id=(
            notification.recipient_user_id if notification is not None else None
        ),
        event_type=notification.event_type if notification is not None else None,
        title=notification.title if notification is not None else None,
        content=notification.content if notification is not None else None,
        status=work_order.status,
        is_read=notification.is_read if notification is not None else None,
        read_at=notification.read_at if notification is not None else None,
        env=work_order.env,
        can_approve=item.can_approve,
        gmt_created=work_order.gmt_created,
        gmt_modified=modified,
    )


@router.post(
    "/openapi/v1/spaces/{space_id}/join-requests",
    status_code=201,
    response_model=Envelope[SpaceJoinRequestCreated],
)
@envelope_errors
async def create_space_join_request(
    space_id: PositiveIdPath,
    body: CreateSpaceJoinRequest,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[SpaceJoinRequestCreated]:
    record = service.create_space_join_request(
        space_id=space_id,
        applicant_user_id=caller_owner_id(principal),
        reason=body.reason,
    )
    return created(
        SpaceJoinRequestCreated(
            work_order_id=record.id,
            work_order_no=record.work_order_no,
            status=record.status,
        ),
        request,
    )


@router.get(
    "/openapi/v1/work-orders",
    response_model=Envelope[Page[WorkOrderListItem]],
)
@envelope_errors
async def list_work_orders(
    request: Request,
    principal: PrincipalDep,
    query_type: Annotated[
        WorkOrderQueryType, Query()
    ] = WorkOrderQueryType.PENDING_FOR_ME,
    item_type: Annotated[WorkOrderItemType, Query()] = WorkOrderItemType.ALL,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[Page[WorkOrderListItem]]:
    total, items = service.list_items(
        actor_id=caller_owner_id(principal),
        query_type=query_type,
        item_type=item_type,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_list_item(item) for item in items], request)


@router.get(
    "/openapi/v1/work-orders/{work_order_id}",
    response_model=Envelope[WorkOrderDetailResponse],
)
@envelope_errors
async def get_work_order(
    work_order_id: PositiveIdPath,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderDetailResponse]:
    detail = service.get_detail(
        work_order_id=work_order_id, actor_id=caller_owner_id(principal)
    )
    work_order = detail.work_order
    return envelope(
        WorkOrderDetailResponse(
            work_order_id=work_order.id,
            work_order_no=work_order.work_order_no,
            biz_type=work_order.biz_type,
            biz_id=detail.space_id,
            event_type=detail.event_type,
            title=detail.title,
            content=WorkOrderDetailContent(
                space_id=detail.space_id,
                space_name=detail.space_name,
                applicant_user_id=work_order.applicant_user_id,
                applicant_name=detail.applicant_name,
                reason=work_order.apply_reason,
            ),
            status=work_order.status,
            reviewer_user_id=work_order.reviewer_user_id,
            review_remark=work_order.review_remark,
            reviewed_at=work_order.reviewed_at,
            can_approve=detail.can_approve,
        ),
        request,
    )


def _review_response(result) -> WorkOrderReviewResponse:
    return WorkOrderReviewResponse(
        work_order_id=result.work_order_id,
        status=result.status,
        reviewer_user_id=result.reviewer_user_id,
        review_remark=result.review_remark,
        reviewed_at=result.reviewed_at,
    )


@router.post(
    "/openapi/v1/work-orders/{work_order_id}/approve",
    response_model=Envelope[WorkOrderReviewResponse],
)
@envelope_errors
async def approve_work_order(
    work_order_id: PositiveIdPath,
    body: WorkOrderReviewRequest,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderReviewResponse]:
    result = service.approve(
        work_order_id=work_order_id,
        actor_id=caller_owner_id(principal),
        review_remark=body.review_remark,
    )
    return envelope(_review_response(result), request)


@router.post(
    "/openapi/v1/work-orders/{work_order_id}/reject",
    response_model=Envelope[WorkOrderReviewResponse],
)
@envelope_errors
async def reject_work_order(
    work_order_id: PositiveIdPath,
    body: WorkOrderReviewRequest,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderReviewResponse]:
    result = service.reject(
        work_order_id=work_order_id,
        actor_id=caller_owner_id(principal),
        review_remark=body.review_remark,
    )
    return envelope(_review_response(result), request)


@router.get(
    "/openapi/v1/work-order-notifications/unread-count",
    response_model=Envelope[UnreadCountResponse],
)
@envelope_errors
async def unread_notification_count(
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[UnreadCountResponse]:
    count = service.unread_count(actor_id=caller_owner_id(principal))
    return envelope(UnreadCountResponse(unread_count=count), request)


@router.post(
    "/openapi/v1/work-order-notifications/read-all",
    response_model=Envelope[NotificationsReadAllResponse],
)
@envelope_errors
async def mark_all_notifications_read(
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[NotificationsReadAllResponse]:
    count = service.mark_all_read(actor_id=caller_owner_id(principal))
    return envelope(NotificationsReadAllResponse(updated_count=count), request)


@router.get(
    "/openapi/v1/work-order-notifications/{notification_id}",
    response_model=Envelope[NotificationDetailResponse],
)
@envelope_errors
async def get_notification(
    notification_id: PositiveIdPath,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[NotificationDetailResponse]:
    detail = service.get_detail(
        notification_id=notification_id, actor_id=caller_owner_id(principal)
    )
    record = detail.notification
    return envelope(
        NotificationDetailResponse(
            notification_id=record.id,
            work_order_id=record.work_order_id,
            notification_category=record.notification_category,
            event_type=record.event_type,
            title=record.title,
            content=record.content,
            is_read=record.is_read,
            work_order_status=detail.work_order_status,
            can_approve=detail.can_approve,
            biz_type=record.biz_type,
            biz_id=record.biz_id,
        ),
        request,
    )


@router.post(
    "/openapi/v1/work-order-notifications/{notification_id}/read",
    response_model=Envelope[NotificationReadResponse],
)
@envelope_errors
async def mark_notification_read(
    notification_id: PositiveIdPath,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[NotificationReadResponse]:
    record = service.mark_read(
        notification_id=notification_id, actor_id=caller_owner_id(principal)
    )
    return envelope(
        NotificationReadResponse(
            notification_id=record.id,
            is_read=record.is_read,
            read_at=record.read_at,
        ),
        request,
    )
