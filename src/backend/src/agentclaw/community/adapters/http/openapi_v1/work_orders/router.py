"""OpenAPI v1 adapter for work orders and recipient notifications."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import GrantNotResolvableError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    caller_owner_id,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.openapi_v1.work_orders.schemas import (
    CreateSpaceJoinRequest,
    CreateWorkOrderEventRequest,
    WorkOrderEventCreated,
    CreateBotEditorRequest,
    BotEditorRequestCreated,
    NotificationDetailResponse,
    NotificationReadResponse,
    NotificationsReadAllResponse,
    SpaceJoinRequestCreated,
    UnreadCountResponse,
    WorkOrderDetailResponse,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderQueryType,
    WorkOrderReviewRequest,
    WorkOrderApprovalRequest,
    WorkOrderReviewResponse,
    WorkOrderLegacyReviewResponse,
)
from agentclaw.community.adapters.http.openapi_v1.work_orders.converter import (
    display_summary,
    display_title,
    json_object,
    preserve_content,
)
from agentclaw.community.adapters.http.work_orders.converter import (
    create_work_order_event_data,
)
from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderDecision as DomainWorkOrderDecision,
    WorkOrderItemType as DomainWorkOrderItemType,
    WorkOrderListItem as DomainListItem,
    WorkOrderQueryType as DomainWorkOrderQueryType,
)
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(tags=["work-orders"], route_class=PublicAPIRoute)
PositiveIdPath = Annotated[int, Path(ge=1, description="Positive numeric identifier.")]
BotIdPath = Annotated[
    str, Path(min_length=1, max_length=64, description="Identifier of the Bot.")
]
PageNoQuery = Annotated[int, Query(ge=1, description="One-based page number.")]
PageSizeQuery = Annotated[
    int, Query(ge=1, le=100, description="Maximum items returned per page.")
]
PrincipalDep = Annotated[Principal, Depends(require_principal)]
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]

_CALLBACK_HEADER_NAMES = {
    "authorization",
    "x-avernet-principal",
    "x-request-id",
    "x-trace-id",
}


def _callback_credential(request: Request) -> WorkOrderCallbackCredential:
    return WorkOrderCallbackCredential(
        headers={
            key: value
            for key, value in request.headers.items()
            if key.lower() in _CALLBACK_HEADER_NAMES
        }
    )


def _require_user_delegation(caller: ActingCaller) -> str:
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        raise GrantNotResolvableError(
            "application holds no live delegation from the named user"
        )
    return caller.user_id


def _list_item(item: DomainListItem) -> WorkOrderListItem:
    work_order = item.work_order
    notification = item.notification
    if work_order is None:
        assert notification is not None
        return WorkOrderListItem(
            item_id=f"NOTIFICATION_{notification.id}",
            item_type=WorkOrderItemType.NOTICE,
            work_order_id=None,
            work_order_no=None,
            notification_id=notification.id,
            notification_category=notification.notification_category,
            biz_type=notification.biz_type,
            biz_id=notification.biz_id,
            applicant_user_id=None,
            apply_reason=None,
            reviewer_user_id=None,
            review_remark=None,
            reviewed_at=None,
            recipient_user_id=notification.recipient_user_id,
            event_type=notification.event_type,
            title=display_title(notification.title, event_type=notification.event_type) or "新的系统通知",
            summary=display_summary(
                notification.event_type,
                notification.content,
                biz_type=notification.biz_type,
                status=(work_order.status if work_order is not None else None),
            ),
            content=preserve_content(notification.content),
            status=None,
            is_read=notification.is_read,
            read_at=notification.read_at,
            env=notification.env,
            can_approve=False,
            gmt_created=notification.gmt_created,
            gmt_modified=notification.gmt_modified,
        )
    created = (
        notification.gmt_created
        if notification is not None
        else work_order.gmt_created
    )
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
    event_type = notification.event_type if notification is not None else None
    title = display_title(
        notification.title if notification is not None else None,
        event_type=event_type,
        biz_type=work_order.biz_type,
        status=work_order.status,
    ) or "新的系统通知"
    summary = display_summary(
        event_type,
        notification.content if notification is not None else None,
        biz_type=work_order.biz_type,
        status=work_order.status,
    )
    content = preserve_content(notification.content) if notification is not None else None
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
        recipient_user_id=notification.recipient_user_id
        if notification is not None
        else None,
        event_type=event_type,
        title=title,
        summary=summary,
        content=content,
        status=work_order.status,
        is_read=notification.is_read if notification is not None else None,
        read_at=notification.read_at if notification is not None else None,
        env=work_order.env,
        can_approve=item.can_approve,
        gmt_created=created,
        gmt_modified=modified,
    )


@router.post(
    "/openapi/v1/bots/{bot_id}/editor-requests",
    status_code=201,
    response_model=Envelope[BotEditorRequestCreated],
)
@envelope_errors
async def create_bot_editor_request(
    bot_id: BotIdPath,
    body: CreateBotEditorRequest,
    request: Request,
    caller: ActingCallerDep,
    owner_id: Annotated[
        str | None,
        Query(
            max_length=256,
            description="Owner of the Bot. Defaults to the current user.",
        ),
    ] = None,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[BotEditorRequestCreated]:
    actor_id = _require_user_delegation(caller)
    record = service.create_bot_editor_request(
        bot_id=bot_id,
        owner_id=owner_id or actor_id,
        applicant_user_id=actor_id,
        reason=body.reason,
    )
    return created(
        BotEditorRequestCreated(
            work_order_id=record.id,
            work_order_no=record.work_order_no,
            status=record.status,
        ),
        request,
    )


@router.post(
    "/openapi/v1/bots/spaces/{space_id}/join-requests",
    status_code=201,
    response_model=Envelope[SpaceJoinRequestCreated],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def create_space_join_request(
    space_id: PositiveIdPath,
    body: CreateSpaceJoinRequest,
    request: Request,
    principal: PrincipalDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[SpaceJoinRequestCreated]:
    actor_id = caller_owner_id(principal)
    record = service.create_space_join_request(
        space_id=space_id,
        applicant_user_id=actor_id,
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


@router.post(
    "/openapi/v1/bots/work-orders/events",
    status_code=201,
    response_model=Envelope[WorkOrderEventCreated],
)
@envelope_errors
async def create_work_order_event(
    body: CreateWorkOrderEventRequest,
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderEventCreated]:
    actor_id = _require_user_delegation(caller)
    logger.info(
        "work-order event received",
        extra={"work_order_event": body.model_dump(mode="json")},
    )
    data = create_work_order_event_data(
        body=body,
        actor_id=actor_id,
        service=service,
    )
    return created(data, request)


@router.get(
    "/openapi/v1/bots/work-orders",
    response_model=Envelope[Page[WorkOrderListItem]],
)
@envelope_errors
async def list_work_orders(
    request: Request,
    caller: ActingCallerDep,
    query_type: Annotated[
        WorkOrderQueryType,
        Query(description="Relationship between the current user and returned items."),
    ] = WorkOrderQueryType.PENDING_FOR_ME,
    item_type: Annotated[
        WorkOrderItemType, Query(description="Category of inbox item to return.")
    ] = WorkOrderItemType.ALL,
    biz_type: Annotated[
        str | None,
        Query(
            max_length=64,
            description="Business type to return, or all business types.",
        ),
    ] = None,
    biz_id: Annotated[
        str | None,
        Query(max_length=128, description="Business identifier to return."),
    ] = None,
    page_no: PageNoQuery = 1,
    page_size: PageSizeQuery = 20,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[Page[WorkOrderListItem]]:
    actor_id = _require_user_delegation(caller)
    total, items = service.list_items(
        actor_id=actor_id,
        query_type=DomainWorkOrderQueryType(query_type),
        item_type=DomainWorkOrderItemType(item_type),
        biz_type=biz_type,
        biz_id=biz_id,
        page_no=page_no,
        page_size=page_size,
    )
    return page(total, [_list_item(item) for item in items], request)


@router.get(
    "/openapi/v1/bots/work-orders/{work_order_id}",
    response_model=Envelope[WorkOrderDetailResponse],
)
@envelope_errors
async def get_work_order(
    work_order_id: PositiveIdPath,
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderDetailResponse]:
    actor_id = _require_user_delegation(caller)
    detail = service.get_detail(work_order_id=work_order_id, actor_id=actor_id)
    work_order = detail.work_order
    return envelope(
        WorkOrderDetailResponse(
            work_order_id=work_order.id,
            work_order_no=work_order.work_order_no,
            biz_type=work_order.biz_type,
            biz_id=detail.space_id
            if work_order.biz_type == "SPACE_JOIN"
            else work_order.biz_id,
            event_type=detail.event_type,
            title=display_title(
                detail.title,
                event_type=detail.event_type,
                biz_type=work_order.biz_type,
                status=work_order.status,
            ) or "新的系统通知",
            summary=display_summary(
                detail.event_type,
                detail.content,
                biz_type=work_order.biz_type,
                status=work_order.status,
            ),
            content=preserve_content(detail.content),
            status=work_order.status,
            reviewer_user_id=work_order.reviewer_user_id,
            reviewer_user_name=detail.reviewer_user_name,
            review_remark=work_order.review_remark,
            reviewed_at=work_order.reviewed_at,
            biz_data=json_object(work_order.biz_data),
            can_approve=detail.can_approve,
        ),
        request,
    )


def _review_response(result) -> WorkOrderReviewResponse:
    return WorkOrderReviewResponse(
        work_order_id=result.work_order_id,
        status=result.status,
        decision=result.decision,
        reviewer_user_id=result.reviewer_user_id,
        review_remark=result.review_remark,
        reviewed_at=result.reviewed_at,
    )


def _legacy_review_response(result) -> WorkOrderLegacyReviewResponse:
    return WorkOrderLegacyReviewResponse(
        work_order_id=result.work_order_id,
        status=result.status,
        reviewer_user_id=result.reviewer_user_id,
        review_remark=result.review_remark,
        reviewed_at=result.reviewed_at,
    )


@router.post(
    "/openapi/v1/bots/work-orders/{work_order_id}/approval",
    response_model=Envelope[WorkOrderReviewResponse],
)
@envelope_errors
async def process_work_order_approval(
    work_order_id: PositiveIdPath,
    body: WorkOrderApprovalRequest,
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderReviewResponse]:
    actor_id = _require_user_delegation(caller)
    result = service.process_approval(
        work_order_id=work_order_id,
        actor_id=actor_id,
        decision=DomainWorkOrderDecision(body.decision.value),
        review_remark=body.review_remark,
        callback_credential=_callback_credential(request),
    )
    return envelope(_review_response(result), request)


@router.post(
    "/openapi/v1/bots/work-orders/{work_order_id}/approve",
    response_model=Envelope[WorkOrderLegacyReviewResponse],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def approve_work_order(
    work_order_id: PositiveIdPath,
    body: WorkOrderReviewRequest,
    request: Request,
    user_id: UserIdDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderReviewResponse]:
    result = service.approve(
        work_order_id=work_order_id, actor_id=user_id, review_remark=body.review_remark
    )
    return envelope(_legacy_review_response(result), request)


@router.post(
    "/openapi/v1/bots/work-orders/{work_order_id}/reject",
    response_model=Envelope[WorkOrderLegacyReviewResponse],
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def reject_work_order(
    work_order_id: PositiveIdPath,
    body: WorkOrderReviewRequest,
    request: Request,
    user_id: UserIdDep,
    service: WorkOrderServiceProtocol = Injected(WorkOrderServiceProtocol),
) -> Envelope[WorkOrderReviewResponse]:
    result = service.reject(
        work_order_id=work_order_id, actor_id=user_id, review_remark=body.review_remark
    )
    return envelope(_legacy_review_response(result), request)


@router.get(
    "/openapi/v1/bots/work-order-notifications/unread-count",
    response_model=Envelope[UnreadCountResponse],
)
@envelope_errors
async def unread_notification_count(
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[UnreadCountResponse]:
    actor_id = _require_user_delegation(caller)
    summary = service.badge_summary(actor_id=actor_id)
    return envelope(UnreadCountResponse(**summary.model_dump()), request)


@router.post(
    "/openapi/v1/bots/work-order-notifications/read-all",
    response_model=Envelope[NotificationsReadAllResponse],
)
@envelope_errors
async def mark_all_notifications_read(
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[NotificationsReadAllResponse]:
    actor_id = _require_user_delegation(caller)
    count = service.mark_all_read(actor_id=actor_id)
    return envelope(NotificationsReadAllResponse(updated_count=count), request)


@router.get(
    "/openapi/v1/bots/work-order-notifications/{notification_id}",
    response_model=Envelope[NotificationDetailResponse],
)
@envelope_errors
async def get_notification(
    notification_id: PositiveIdPath,
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[NotificationDetailResponse]:
    actor_id = _require_user_delegation(caller)
    detail = service.get_detail(notification_id=notification_id, actor_id=actor_id)
    record = detail.notification
    return envelope(
        NotificationDetailResponse(
            notification_id=record.id,
            work_order_id=record.work_order_id,
            notification_category=record.notification_category,
            event_type=record.event_type,
            title=display_title(
                record.title,
                event_type=record.event_type,
                biz_type=record.biz_type,
                status=detail.work_order_status,
            ) or "新的系统通知",
            summary=display_summary(
                record.event_type,
                record.content,
                biz_type=record.biz_type,
                status=detail.work_order_status,
            ),
            content=preserve_content(record.content),
            is_read=record.is_read,
            work_order_status=detail.work_order_status,
            can_approve=detail.can_approve,
            biz_type=record.biz_type,
            biz_id=record.biz_id,
        ),
        request,
    )


@router.post(
    "/openapi/v1/bots/work-order-notifications/{notification_id}/read",
    response_model=Envelope[NotificationReadResponse],
)
@envelope_errors
async def mark_notification_read(
    notification_id: PositiveIdPath,
    request: Request,
    caller: ActingCallerDep,
    service: WorkOrderNotificationServiceProtocol = Injected(
        WorkOrderNotificationServiceProtocol
    ),
) -> Envelope[NotificationReadResponse]:
    actor_id = _require_user_delegation(caller)
    record = service.mark_read(notification_id=notification_id, actor_id=actor_id)
    return envelope(
        NotificationReadResponse(
            notification_id=record.id,
            is_read=record.is_read,
            read_at=record.read_at,
        ),
        request,
    )
