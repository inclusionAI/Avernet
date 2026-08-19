"""Domain records, persisted enums, query enums and message templates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class WorkOrderStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class WorkOrderBizType(StrEnum):
    SPACE_JOIN = "SPACE_JOIN"


class NotificationCategory(StrEnum):
    APPROVAL = "APPROVAL"
    NOTICE = "NOTICE"


class WorkOrderQueryType(StrEnum):
    PENDING_FOR_ME = "PENDING_FOR_ME"
    INITIATED_BY_ME = "INITIATED_BY_ME"
    PROCESSED_BY_ME = "PROCESSED_BY_ME"


class WorkOrderItemType(StrEnum):
    ALL = "ALL"
    APPROVAL = "APPROVAL"
    NOTICE = "NOTICE"


class WorkOrderEventType(StrEnum):
    SPACE_JOIN_APPLIED = "SPACE_JOIN_APPLIED"
    SPACE_JOIN_REVIEWED = "SPACE_JOIN_REVIEWED"
    SPACE_MEMBER_ADDED = "SPACE_MEMBER_ADDED"
    BOT_COLLABORATOR_APPLIED = "BOT_COLLABORATOR_APPLIED"
    BOT_COLLABORATOR_REVIEWED = "BOT_COLLABORATOR_REVIEWED"
    BOT_MEMBER_ADDED = "BOT_MEMBER_ADDED"
    HUMAN2BOT_FRIEND_APPLIED = "HUMAN2BOT_FRIEND_APPLIED"
    HUMAN2BOT_FRIEND_REVIEWED = "HUMAN2BOT_FRIEND_REVIEWED"
    BOT2BOT_FRIEND_APPLIED = "BOT2BOT_FRIEND_APPLIED"
    BOT2BOT_FRIEND_REVIEWED = "BOT2BOT_FRIEND_REVIEWED"
    HUMAN2BOT_PUBLIC_ORDER_CREATED = "HUMAN2BOT_PUBLIC_ORDER_CREATED"
    HUMAN2BOT_PUBLIC_ORDER_COMPLETED = "HUMAN2BOT_PUBLIC_ORDER_COMPLETED"
    BOT2BOT_PUBLIC_ORDER_CREATED = "BOT2BOT_PUBLIC_ORDER_CREATED"
    BOT2BOT_PUBLIC_ORDER_COMPLETED = "BOT2BOT_PUBLIC_ORDER_COMPLETED"


EVENT_CATEGORIES: dict[WorkOrderEventType, NotificationCategory] = {
    WorkOrderEventType.SPACE_JOIN_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.SPACE_JOIN_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.SPACE_MEMBER_ADDED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT_COLLABORATOR_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.BOT_COLLABORATOR_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT_MEMBER_ADDED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_FRIEND_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_CREATED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_COMPLETED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_CREATED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_COMPLETED: NotificationCategory.NOTICE,
}


class WorkOrderMessageTitle(StrEnum):
    SPACE_JOIN_PENDING = "空间加入申请待审批"
    SPACE_JOIN_APPROVED = "空间加入申请已通过"
    SPACE_JOIN_REJECTED = "空间加入申请未通过"
    SPACE_MEMBER_ADDED = "你已被添加到空间"


class WorkOrderMessageContent(StrEnum):
    SPACE_JOIN_PENDING = (
        "用户「{applicant_name}」申请加入空间「{space_name}」，请及时处理。"
    )
    SPACE_JOIN_APPROVED = "你加入空间「{space_name}」的申请已通过。"
    SPACE_JOIN_REJECTED = (
        "你加入空间「{space_name}」的申请未通过。拒绝原因：{review_remark}"
    )
    SPACE_MEMBER_ADDED = "你已被添加到空间「{space_name}」。"


class WorkOrderRecord(BaseModel):
    id: int
    work_order_no: str
    biz_type: WorkOrderBizType
    biz_id: str
    applicant_user_id: str
    apply_reason: str | None
    status: WorkOrderStatus
    reviewer_user_id: str | None
    review_remark: str | None
    reviewed_at: datetime | None
    env: str
    gmt_created: datetime
    gmt_modified: datetime


class WorkOrderNotificationRecord(BaseModel):
    id: int
    work_order_id: int | None
    recipient_user_id: str
    notification_category: NotificationCategory
    event_type: WorkOrderEventType
    biz_type: WorkOrderBizType
    biz_id: str
    title: str
    content: str | None
    is_read: bool
    read_at: datetime | None
    env: str
    gmt_created: datetime
    gmt_modified: datetime


class WorkOrderNotificationDraft(BaseModel):
    recipient_user_id: str
    notification_category: NotificationCategory
    event_type: WorkOrderEventType
    biz_type: WorkOrderBizType
    biz_id: str
    title: str
    content: str


class WorkOrderNotificationDetail(BaseModel):
    notification: WorkOrderNotificationRecord
    work_order_status: WorkOrderStatus | None
    can_approve: bool


class WorkOrderNotificationBadgeSummary(BaseModel):
    unread_count: int
    pending_approval_count: int
    unread_notice_count: int
    badge_count: int


class WorkOrderListItem(BaseModel):
    work_order: WorkOrderRecord
    notification: WorkOrderNotificationRecord | None
    can_approve: bool


class WorkOrderDetail(BaseModel):
    work_order: WorkOrderRecord
    event_type: WorkOrderEventType
    title: str
    space_id: int
    space_name: str
    applicant_name: str
    can_approve: bool


class WorkOrderReviewResult(BaseModel):
    work_order_id: int
    status: WorkOrderStatus
    reviewer_user_id: str
    review_remark: str | None
    reviewed_at: datetime
