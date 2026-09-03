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
    """Known notification events and their complete display contract.

    Keeping the category, title, and summary beside the event value prevents
    the event catalogue and presentation mappings from drifting apart.
    """

    def __new__(
        cls,
        value: str,
        category: NotificationCategory,
        title: str,
        summary: str,
    ):
        member = str.__new__(cls, value)
        member._value_ = value
        member.notification_category = category
        member.title = title
        member.summary = summary
        return member

    SPACE_JOIN_APPLIED = (
        "SPACE_JOIN_APPLIED", NotificationCategory.APPROVAL,
        "空间加入申请待审批", "有新的空间加入申请，请及时处理。",
    )
    SPACE_JOIN_REVIEWED = (
        "SPACE_JOIN_REVIEWED", NotificationCategory.NOTICE,
        "空间加入申请已处理", "空间加入申请已有处理结果，请查看详情。",
    )
    SPACE_MEMBER_ADDED = (
        "SPACE_MEMBER_ADDED", NotificationCategory.NOTICE,
        "你已被添加到空间", "你已加入一个新的空间，请查看详情。",
    )
    SPACE_MEMBER_REMOVED = (
        "SPACE_MEMBER_REMOVED", NotificationCategory.NOTICE,
        "你已被移出空间", "你已被移出一个空间，请查看详情。",
    )
    BOT_COLLABORATOR_APPLIED = (
        "BOT_COLLABORATOR_APPLIED", NotificationCategory.APPROVAL,
        "Bot 共同编辑申请待审批", "有新的 Bot 共同编辑申请，请及时处理。",
    )
    BOT_COLLABORATOR_REVIEWED = (
        "BOT_COLLABORATOR_REVIEWED", NotificationCategory.NOTICE,
        "Bot 共同编辑申请已处理", "Bot 共同编辑申请已有处理结果，请查看详情。",
    )
    BOT_MEMBER_ADDED = (
        "BOT_MEMBER_ADDED", NotificationCategory.NOTICE,
        "你已被添加为 Bot 协作者", "你已获得一个 Bot 的协作权限，请查看详情。",
    )
    SKILL_COLLABORATOR_APPLIED = (
        "SKILL_COLLABORATOR_APPLIED", NotificationCategory.APPROVAL,
        "Skill 共同编辑申请待审批", "有新的 Skill 共同编辑申请，请及时处理。",
    )
    SKILL_COLLABORATOR_REVIEWED = (
        "SKILL_COLLABORATOR_REVIEWED", NotificationCategory.NOTICE,
        "Skill 共同编辑申请已处理", "Skill 共同编辑申请已有处理结果，请查看详情。",
    )
    SKILL_MEMBER_ADDED = (
        "SKILL_MEMBER_ADDED", NotificationCategory.NOTICE,
        "你已被添加为 Skill 协作者", "你已获得一个 Skill 的协作权限，请查看详情。",
    )
    HUMAN2BOT_FRIEND_APPLIED = (
        "HUMAN2BOT_FRIEND_APPLIED", NotificationCategory.APPROVAL,
        "人机好友申请待审批", "有新的人机好友申请，请及时处理。",
    )
    HUMAN2BOT_FRIEND_REVIEWED = (
        "HUMAN2BOT_FRIEND_REVIEWED", NotificationCategory.NOTICE,
        "人机好友申请已处理", "人机好友申请已有处理结果，请查看详情。",
    )
    BOT2BOT_FRIEND_APPLIED = (
        "BOT2BOT_FRIEND_APPLIED", NotificationCategory.APPROVAL,
        "Bot 好友申请待审批", "有新的 Bot 好友申请，请及时处理。",
    )
    BOT2BOT_FRIEND_REVIEWED = (
        "BOT2BOT_FRIEND_REVIEWED", NotificationCategory.NOTICE,
        "Bot 好友申请已处理", "Bot 好友申请已有处理结果，请查看详情。",
    )
    HUMAN2BOT_PUBLIC_ORDER_CREATED = (
        "HUMAN2BOT_PUBLIC_ORDER_CREATED", NotificationCategory.NOTICE,
        "人机公开订单已创建", "你有一条新的人机公开订单，请查看详情。",
    )
    HUMAN2BOT_PUBLIC_ORDER_COMPLETED = (
        "HUMAN2BOT_PUBLIC_ORDER_COMPLETED", NotificationCategory.NOTICE,
        "人机公开订单已完成", "一条人机公开订单已完成，请查看详情。",
    )
    BOT2BOT_PUBLIC_ORDER_CREATED = (
        "BOT2BOT_PUBLIC_ORDER_CREATED", NotificationCategory.NOTICE,
        "Bot 公开订单已创建", "你有一条新的 Bot 公开订单，请查看详情。",
    )
    BOT2BOT_PUBLIC_ORDER_COMPLETED = (
        "BOT2BOT_PUBLIC_ORDER_COMPLETED", NotificationCategory.NOTICE,
        "Bot 公开订单已完成", "一条 Bot 公开订单已完成，请查看详情。",
    )
    TASK_DISCOVERED = (
        "TASK_DISCOVERED", NotificationCategory.NOTICE,
        "发现新任务", "发现一条新任务，请查看详情。",
    )


EVENT_CATEGORIES: dict[WorkOrderEventType, NotificationCategory] = {
    event_type: event_type.notification_category for event_type in WorkOrderEventType
}


def event_type_definition(event_type: str | None) -> WorkOrderEventType | None:
    """Return the canonical definition, or ``None`` for external/legacy values."""
    try:
        return WorkOrderEventType(event_type) if event_type else None
    except ValueError:
        return None


def notification_title_for(
    event_type: str | None, fallback_title: str | None = None
) -> str:
    """Return the canonical title while retaining a useful legacy fallback."""
    definition = event_type_definition(event_type)
    if definition is not None:
        return definition.title
    return fallback_title or "新的系统通知"


def notification_summary_for(event_type: str | None) -> str:
    """Return a non-empty summary for every known and unknown event."""
    definition = event_type_definition(event_type)
    return definition.summary if definition is not None else "你有一条新的通知，请查看详情。"


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


def skill_collaborator_applicant_display(
    *, applicant_user_id: str, applicant_name: str
) -> str:
    """Format the stable identity shown in a Skill editor request."""

    normalized_name = applicant_name.strip()
    if not normalized_name or normalized_name == applicant_user_id:
        return f"「{applicant_user_id}」"
    return f"「{normalized_name}」({applicant_user_id})"


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
    event_type: str | None
    title: str | None
    content: str | None = None
    space_id: int
    space_name: str
    applicant_name: str
    reviewer_user_name: str | None = None
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
