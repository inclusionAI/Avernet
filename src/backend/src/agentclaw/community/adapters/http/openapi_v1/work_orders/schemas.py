"""HTTP request and response schemas for work orders and notifications."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer

from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderStatus,
)


def _utc_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class _UtcResponseModel(BaseModel):
    @field_serializer(
        "reviewed_at",
        "read_at",
        "gmt_created",
        "gmt_modified",
        check_fields=False,
        when_used="json",
    )
    def _serialize_utc_datetime(self, value: datetime | None) -> str | None:
        return _utc_datetime(value)


class CreateSpaceJoinRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class SpaceJoinRequestCreated(BaseModel):
    work_order_id: int
    work_order_no: str
    status: WorkOrderStatus


class WorkOrderReviewRequest(BaseModel):
    review_remark: str = Field(min_length=1, max_length=512)


class WorkOrderReviewResponse(_UtcResponseModel):
    work_order_id: int
    status: WorkOrderStatus
    reviewer_user_id: str
    review_remark: str
    reviewed_at: datetime


class WorkOrderListItem(_UtcResponseModel):
    item_id: str
    item_type: WorkOrderItemType
    work_order_id: int
    work_order_no: str
    notification_id: int | None
    notification_category: NotificationCategory | None
    biz_type: WorkOrderBizType
    biz_id: str
    applicant_user_id: str
    apply_reason: str | None
    reviewer_user_id: str | None
    review_remark: str | None
    reviewed_at: datetime | None
    recipient_user_id: str | None
    event_type: WorkOrderEventType | None
    title: str | None
    content: str | None
    status: WorkOrderStatus
    is_read: bool | None
    read_at: datetime | None
    env: str
    can_approve: bool
    gmt_created: datetime
    gmt_modified: datetime


class WorkOrderDetailContent(BaseModel):
    space_id: int
    space_name: str
    applicant_user_id: str
    applicant_name: str
    reason: str | None


class WorkOrderDetailResponse(_UtcResponseModel):
    work_order_id: int
    work_order_no: str
    biz_type: WorkOrderBizType
    biz_id: int
    event_type: WorkOrderEventType
    title: str
    content: WorkOrderDetailContent
    status: WorkOrderStatus
    reviewer_user_id: str | None
    review_remark: str | None
    reviewed_at: datetime | None
    can_approve: bool


class NotificationDetailResponse(BaseModel):
    notification_id: int
    work_order_id: int | None
    notification_category: NotificationCategory
    event_type: WorkOrderEventType
    title: str
    content: str | None
    is_read: bool
    work_order_status: WorkOrderStatus | None
    can_approve: bool
    biz_type: WorkOrderBizType
    biz_id: str


class UnreadCountResponse(BaseModel):
    unread_count: int


class NotificationReadResponse(_UtcResponseModel):
    notification_id: int
    is_read: bool
    read_at: datetime | None


class NotificationsReadAllResponse(BaseModel):
    updated_count: int
