"""Domain records, persisted enums, query enums and message templates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class WorkOrderStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class WorkOrderEventStatus(StrEnum):
    PENDING = "PENDING"
    CREATED = "CREATED"


class WorkOrderDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class WorkOrderApproverStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class WorkOrderBizType(StrEnum):
    SPACE_JOIN = "SPACE_JOIN"
    BOT_COLLABORATOR = "BOT_COLLABORATOR"
    SKILL_COLLABORATOR = "SKILL_COLLABORATOR"
    BOT_FRIEND = "BOT_FRIEND"


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
    SKILL_COLLABORATOR_APPLIED = "SKILL_COLLABORATOR_APPLIED"
    SKILL_COLLABORATOR_REVIEWED = "SKILL_COLLABORATOR_REVIEWED"
    SKILL_MEMBER_ADDED = "SKILL_MEMBER_ADDED"
    HUMAN2BOT_FRIEND_APPLIED = "HUMAN2BOT_FRIEND_APPLIED"
    HUMAN2BOT_FRIEND_REVIEWED = "HUMAN2BOT_FRIEND_REVIEWED"
    BOT2BOT_FRIEND_APPLIED = "BOT2BOT_FRIEND_APPLIED"
    BOT2BOT_FRIEND_REVIEWED = "BOT2BOT_FRIEND_REVIEWED"
    HUMAN2BOT_PUBLIC_ORDER_CREATED = "HUMAN2BOT_PUBLIC_ORDER_CREATED"
    HUMAN2BOT_PUBLIC_ORDER_COMPLETED = "HUMAN2BOT_PUBLIC_ORDER_COMPLETED"
    BOT2BOT_PUBLIC_ORDER_CREATED = "BOT2BOT_PUBLIC_ORDER_CREATED"
    BOT2BOT_PUBLIC_ORDER_COMPLETED = "BOT2BOT_PUBLIC_ORDER_COMPLETED"
    SPACE_MEMBER_REMOVED = "SPACE_MEMBER_REMOVED"
    TASK_DISCOVERED = "TASK_DISCOVERED"


EVENT_CATEGORIES: dict[WorkOrderEventType, NotificationCategory] = {
    WorkOrderEventType.SPACE_JOIN_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.SPACE_JOIN_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.SPACE_MEMBER_ADDED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT_COLLABORATOR_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.BOT_COLLABORATOR_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT_MEMBER_ADDED: NotificationCategory.NOTICE,
    WorkOrderEventType.SKILL_COLLABORATOR_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.SKILL_MEMBER_ADDED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_FRIEND_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_CREATED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_COMPLETED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_CREATED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_COMPLETED: NotificationCategory.NOTICE,
    WorkOrderEventType.SPACE_MEMBER_REMOVED: NotificationCategory.NOTICE,
    WorkOrderEventType.TASK_DISCOVERED: NotificationCategory.NOTICE,
}

# Approval events are extracted from the single classification table so new
# event values only need one category entry above.
APPROVAL_EVENT_TYPES: frozenset[WorkOrderEventType] = frozenset(
    event_type
    for event_type, category in EVENT_CATEGORIES.items()
    if category is NotificationCategory.APPROVAL
)
FRIEND_APPROVAL_EVENT_TYPES: frozenset[WorkOrderEventType] = frozenset(
    {
        WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED,
        WorkOrderEventType.BOT2BOT_FRIEND_APPLIED,
    }
)
REVIEWED_EVENT_TYPES: dict[WorkOrderEventType, WorkOrderEventType] = {
    WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED: (
        WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED
    ),
    WorkOrderEventType.BOT2BOT_FRIEND_APPLIED: (
        WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED
    ),
}


def reviewed_event_type_for(*, source_event_type: str | None, biz_type: str) -> str:
    """Resolve the notice event produced after an approval decision."""

    try:
        mapped = REVIEWED_EVENT_TYPES.get(WorkOrderEventType(source_event_type))
    except (TypeError, ValueError):
        mapped = None
    return mapped.value if mapped is not None else f"{biz_type}_REVIEWED"


NOTICE_EVENT_TYPES: frozenset[WorkOrderEventType] = frozenset(
    event_type
    for event_type, category in EVENT_CATEGORIES.items()
    if category is NotificationCategory.NOTICE
)


class WorkOrderTitleKey(StrEnum):
    """Stable title codes persisted for Space-join notifications.

    The database stores these language-independent codes. Delivery adapters
    translate them into display copy and retain compatibility with historical
    Chinese titles and the former ``SPACE_JOIN APPROVED`` format.
    """

    SPACE_JOIN_PENDING = "SPACE_JOIN_PENDING"
    SPACE_JOIN_APPROVED = "SPACE_JOIN_APPROVED"
    SPACE_JOIN_REJECTED = "SPACE_JOIN_REJECTED"


class WorkOrderMessageTitle(StrEnum):
    """Chinese display copy returned by delivery adapters."""

    SPACE_JOIN_PENDING = "空间加入申请待审批"
    SPACE_JOIN_APPROVED = "空间加入申请已通过"
    SPACE_JOIN_REJECTED = "空间加入申请未通过"
    SPACE_MEMBER_ADDED = "你已被添加到空间"
    SPACE_MEMBER_REMOVED = "你已被移出空间"
    BOT_COLLABORATOR_PENDING = "Bot 共同编辑申请待审批"
    BOT_COLLABORATOR_APPROVED = "Bot 共同编辑申请已通过"
    BOT_COLLABORATOR_REJECTED = "Bot 共同编辑申请未通过"
    SKILL_COLLABORATOR_PENDING = "Skill 共同编辑申请待审批"
    SKILL_COLLABORATOR_APPROVED = "Skill 共同编辑申请已通过"
    SKILL_COLLABORATOR_REJECTED = "Skill 共同编辑申请未通过"


class WorkOrderMessageContent(StrEnum):
    SPACE_JOIN_PENDING = (
        "用户「{applicant_name}」申请加入空间「{space_name}」，请及时处理。"
    )
    SPACE_JOIN_APPROVED = "你加入空间「{space_name}」的申请已通过。"
    SPACE_JOIN_REJECTED = (
        "你加入空间「{space_name}」的申请未通过。拒绝原因：{review_remark}"
    )
    SPACE_MEMBER_ADDED = "你已被添加到空间「{space_name}」。"
    SPACE_MEMBER_REMOVED = "你已被移出空间「{space_name}」。"
    BOT_COLLABORATOR_PENDING = (
        "用户「{applicant_name}」申请共同编辑 Bot「{bot_name}」，请及时处理。"
    )
    BOT_COLLABORATOR_APPROVED = "你共同编辑 Bot「{bot_name}」的申请已通过。"
    BOT_COLLABORATOR_REJECTED = (
        "你共同编辑 Bot「{bot_name}」的申请未通过。拒绝原因：{review_remark}"
    )
    SKILL_COLLABORATOR_PENDING = (
        "用户{applicant_display}申请共同编辑 Skill「{skill_name}」，请及时处理。"
    )
    SKILL_COLLABORATOR_APPROVED = "你共同编辑 Skill「{skill_name}」的申请已通过。"
    SKILL_COLLABORATOR_REJECTED = (
        "你共同编辑 Skill「{skill_name}」的申请未通过。拒绝原因：{review_remark}"
    )


class WorkOrderApproverRecord(BaseModel):
    id: int
    work_order_id: int
    approver_user_id: str
    status: WorkOrderApproverStatus
    review_remark: str | None
    reviewed_at: datetime | None
    env: str
    gmt_created: datetime
    gmt_modified: datetime


class WorkOrderRecord(BaseModel):
    id: int
    work_order_no: str
    biz_type: str
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
    biz_data: str | None = None


class WorkOrderNotificationRecord(BaseModel):
    id: int
    work_order_id: int | None
    recipient_user_id: str
    notification_category: NotificationCategory
    event_type: str
    biz_type: str
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
    event_type: str
    biz_type: str
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
    work_order: WorkOrderRecord | None
    notification: WorkOrderNotificationRecord | None
    can_approve: bool


class WorkOrderDetail(BaseModel):
    work_order: WorkOrderRecord
    event_type: str
    title: str
    content: str | None = None
    space_id: int
    space_name: str
    applicant_name: str
    can_approve: bool


class WorkOrderApprovalContext(BaseModel):
    """Canonical source event and state used before an external callback."""

    work_order: WorkOrderRecord
    approver: WorkOrderApproverRecord
    source_event_type: str | None


class WorkOrderEventCreatedResult(BaseModel):
    event_category: NotificationCategory
    work_order_id: int | None
    work_order_no: str | None
    notification_ids: list[int]
    status: WorkOrderEventStatus


class WorkOrderReviewResult(BaseModel):
    work_order_id: int
    status: WorkOrderStatus
    reviewer_user_id: str
    review_remark: str | None
    reviewed_at: datetime
    decision: WorkOrderDecision = WorkOrderDecision.APPROVED
