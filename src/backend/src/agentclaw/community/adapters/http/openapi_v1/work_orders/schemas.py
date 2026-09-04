"""HTTP request and response schemas for work orders and notifications."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_serializer

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class WorkOrderDecision(_DocumentedEnum):
    """Decision submitted for a pending work order."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    __descriptions__ = {
        "APPROVED": "Approve the work order.",
        "REJECTED": "Reject the work order.",
    }


class WorkOrderStatus(_DocumentedEnum):
    """Current processing state of a work order."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    __descriptions__ = {
        "PENDING": "Awaiting review.",
        "APPROVED": "Approved by an authorized reviewer.",
        "REJECTED": "Rejected by an authorized reviewer.",
    }


class WorkOrderBizType(_DocumentedEnum):
    """Business object represented by a work order."""

    SPACE_JOIN = "SPACE_JOIN"
    BOT_COLLABORATOR = "BOT_COLLABORATOR"
    SKILL_COLLABORATOR = "SKILL_COLLABORATOR"
    BOT_FRIEND = "BOT_FRIEND"

    __descriptions__ = {
        "SPACE_JOIN": "A request to join a Space.",
        "BOT_COLLABORATOR": "A request to jointly edit a Bot.",
        "SKILL_COLLABORATOR": "A request to jointly edit a Space Skill.",
        "BOT_FRIEND": "A Human-to-Bot or Bot-to-Bot friend request.",
    }


class NotificationCategory(_DocumentedEnum):
    """How a notification is presented to its recipient."""

    APPROVAL = "APPROVAL"
    NOTICE = "NOTICE"

    __descriptions__ = {
        "APPROVAL": "Requires a review decision from the recipient.",
        "NOTICE": "Reports an event and requires no review decision.",
    }


class WorkOrderEventStatus(_DocumentedEnum):
    """Persistence state returned after a unified event is accepted."""

    PENDING = "PENDING"
    CREATED = "CREATED"

    __descriptions__ = {
        "PENDING": "An approval work order is waiting for review.",
        "CREATED": "A notice notification was created.",
    }


class CreateWorkOrderEventRequest(BaseModel):
    """Generic request for creating an approval work order or notice."""

    event_category: NotificationCategory = Field(
        description="Whether the event requires approval or is informational."
    )
    biz_type: str = Field(
        min_length=1,
        max_length=64,
        description="Business type represented by the event.",
    )
    biz_id: str = Field(
        min_length=1,
        max_length=128,
        description="Identifier of the related business object.",
    )
    event_type: str = Field(
        min_length=1,
        max_length=64,
        description="Business event that triggered the work-order event.",
    )
    applicant_user_id: str | None = Field(
        default=None,
        max_length=256,
        description="Applicant user identifier, when applicable.",
    )
    approver_user_ids: list[str] = Field(
        default_factory=list, description="User identifiers who may approve the event."
    )
    recipient_user_ids: list[str] = Field(
        default_factory=list,
        description="User identifiers who receive the event notice.",
    )
    title: str = Field(
        min_length=1, max_length=256, description="Display title of the event."
    )
    content: dict[str, Any] | None = Field(
        default=None,
        description="JSON object stored as notification content without business reshaping.",
    )
    apply_reason: str | None = Field(
        default=None,
        max_length=512,
        description="Reason supplied for an approval event, when applicable.",
    )
    biz_data: dict[str, Any] | None = Field(
        default=None,
        description="JSON object stored as work-order business data without business reshaping.",
    )


class WorkOrderEventCreated(BaseModel):
    """Identifiers and state created by the unified event endpoint."""

    event_category: NotificationCategory = Field(
        description="Whether the created item requires approval or is informational."
    )
    work_order_id: int | None = Field(
        description="Created work-order identifier, when an approval item was created."
    )
    work_order_no: str | None = Field(
        description="Human-readable work-order number, when one exists."
    )
    notification_ids: list[int] = Field(
        description="Identifiers of notifications created for recipients."
    )
    status: WorkOrderEventStatus = Field(
        description="Resulting state of the accepted event."
    )


class WorkOrderQueryType(_DocumentedEnum):
    """Relationship between the current user and listed work orders."""

    PENDING_FOR_ME = "PENDING_FOR_ME"
    INITIATED_BY_ME = "INITIATED_BY_ME"
    PROCESSED_BY_ME = "PROCESSED_BY_ME"

    __descriptions__ = {
        "PENDING_FOR_ME": "Pending work orders awaiting the current user's review.",
        "INITIATED_BY_ME": "Work orders submitted by the current user.",
        "PROCESSED_BY_ME": "Completed approvals and informational notices already read by the current user.",
    }


class WorkOrderItemType(_DocumentedEnum):
    """Category of item included in a work-order list."""

    ALL = "ALL"
    APPROVAL = "APPROVAL"
    NOTICE = "NOTICE"

    __descriptions__ = {
        "ALL": "Both approval items and informational notices.",
        "APPROVAL": "Items that represent reviewable work orders.",
        "NOTICE": "Items that represent informational notifications.",
    }


class WorkOrderEventType(_DocumentedEnum):
    """Business event that created a work order or notification."""

    SPACE_JOIN_APPLIED = "SPACE_JOIN_APPLIED"
    SPACE_JOIN_REVIEWED = "SPACE_JOIN_REVIEWED"
    SPACE_MEMBER_ADDED = "SPACE_MEMBER_ADDED"
    SPACE_MEMBER_REMOVED = "SPACE_MEMBER_REMOVED"
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

    __descriptions__ = {
        "SPACE_JOIN_APPLIED": "A user submitted a request to join a Space.",
        "SPACE_JOIN_REVIEWED": "A Space join request received a review decision.",
        "SPACE_MEMBER_ADDED": "A user was directly added to a Space.",
        "SPACE_MEMBER_REMOVED": "A user was removed from a Space.",
        "BOT_COLLABORATOR_APPLIED": "A bot collaborator request was submitted.",
        "BOT_COLLABORATOR_REVIEWED": "A bot collaborator request was reviewed.",
        "BOT_MEMBER_ADDED": "A member was directly added to a bot.",
        "SKILL_COLLABORATOR_APPLIED": "A skill collaborator request was submitted.",
        "SKILL_COLLABORATOR_REVIEWED": "A skill collaborator request was reviewed.",
        "SKILL_MEMBER_ADDED": "A member was directly added to a skill.",
        "HUMAN2BOT_FRIEND_APPLIED": "A user-to-bot friend request was submitted.",
        "HUMAN2BOT_FRIEND_REVIEWED": "A user-to-bot friend request was reviewed.",
        "BOT2BOT_FRIEND_APPLIED": "A bot-to-bot friend request was submitted.",
        "BOT2BOT_FRIEND_REVIEWED": "A bot-to-bot friend request was reviewed.",
        "HUMAN2BOT_PUBLIC_ORDER_CREATED": "A user created a public order for a bot.",
        "HUMAN2BOT_PUBLIC_ORDER_COMPLETED": "A user's public bot order completed.",
        "BOT2BOT_PUBLIC_ORDER_CREATED": "A bot created a public order for another bot.",
        "BOT2BOT_PUBLIC_ORDER_COMPLETED": "A bot-to-bot public order completed.",
    }


def _database_datetime(value: datetime | None) -> str | None:
    """Serialize database clock values without adding a timezone marker.

    Work-order timestamps follow the repository convention: the database
    writes its current time into timezone-less DATETIME columns.  Keep that
    clock value unchanged for the frontend instead of relabelling it as UTC.
    """
    if value is None:
        return None
    return value.replace(tzinfo=None).isoformat()


class _UtcResponseModel(BaseModel):
    @field_serializer(
        "reviewed_at",
        "read_at",
        "gmt_created",
        "gmt_modified",
        check_fields=False,
        when_used="json",
    )
    def _serialize_database_datetime(self, value: datetime | None) -> str | None:
        return _database_datetime(value)


class CreateSpaceJoinRequest(BaseModel):
    """Request for joining a Space."""

    reason: str | None = Field(
        default=None,
        max_length=512,
        description="Optional reason for requesting membership.",
    )


class SpaceJoinRequestCreated(BaseModel):
    """Work-order identity returned for a new Space join request."""

    work_order_id: int = Field(description="Identifier of the created work order.")
    work_order_no: str | None = Field(
        description="Human-readable work-order number, when one exists."
    )
    status: WorkOrderStatus = Field(description="Initial work-order status.")


class CreateBotEditorRequest(BaseModel):
    """Request for jointly editing a Team Space Bot."""

    reason: str = Field(
        min_length=1, max_length=512, description="Reason for requesting edit access."
    )


class BotEditorRequestCreated(BaseModel):
    """Work-order identity returned for a Bot editor request."""

    work_order_id: int = Field(description="Identifier of the created work order.")
    work_order_no: str = Field(description="Human-readable work-order number.")
    status: WorkOrderStatus = Field(description="Initial work-order status.")


class WorkOrderApprovalRequest(BaseModel):
    """Decision submitted to the unified work-order approval endpoint."""

    decision: WorkOrderDecision = Field(description="Approval decision.")
    review_remark: str | None = Field(
        default=None,
        max_length=512,
        description="Approval comment; required for rejection.",
    )


class WorkOrderReviewRequest(BaseModel):
    """Review comment supplied with an approval or rejection."""

    review_remark: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Optional approval comment. Rejection requires a non-blank comment."
        ),
    )


class WorkOrderReviewResponse(_UtcResponseModel):
    """Final state returned after reviewing a work order."""

    work_order_id: int = Field(description="Identifier of the reviewed work order.")
    status: WorkOrderStatus = Field(description="Status after the review decision.")
    decision: WorkOrderDecision = Field(
        description="Decision recorded for the work order."
    )
    reviewer_user_id: str = Field(description="Identifier of the reviewer.")
    review_remark: str | None = Field(
        description="Reviewer comment, or null for approval without a comment."
    )
    reviewed_at: datetime = Field(
        description="UTC time when the review decision was recorded."
    )


class WorkOrderLegacyReviewResponse(_UtcResponseModel):
    """Compatibility response returned by the legacy review endpoints."""

    work_order_id: int = Field(description="Identifier of the reviewed work order.")
    status: WorkOrderStatus = Field(description="Status after the review decision.")
    reviewer_user_id: str = Field(description="Identifier of the reviewer.")
    review_remark: str | None = Field(
        description="Reviewer comment, or null when no comment was supplied."
    )
    reviewed_at: datetime = Field(
        description="UTC time when the review decision was recorded."
    )


class WorkOrderListItem(_UtcResponseModel):
    """Approval or notification item in a user's work-order inbox."""

    item_id: str = Field(description="Stable identifier of this combined list item.")
    item_type: WorkOrderItemType = Field(description="Category of the list item.")
    work_order_id: int | None = Field(
        description="Related work-order identifier, when one exists."
    )
    work_order_no: str | None = Field(
        description="Human-readable work-order number, when one exists."
    )
    notification_id: int | None = Field(
        description="Related notification identifier, or null for approval-only items."
    )
    notification_category: NotificationCategory | None = Field(
        description="Notification category, or null when no notification is attached."
    )
    biz_type: str = Field(description="Business type supplied by the business module.")
    biz_id: int | str = Field(description="Identifier of the related business object.")
    applicant_user_id: str | None = Field(
        description="Identifier of the applicant, when one exists."
    )
    apply_reason: str | None = Field(description="Application reason, when supplied.")
    reviewer_user_id: str | None = Field(
        description="Identifier of the reviewer after processing, otherwise null."
    )
    review_remark: str | None = Field(
        description="Review comment after processing, otherwise null."
    )
    reviewed_at: datetime | None = Field(
        description="UTC review time after processing, otherwise null."
    )
    recipient_user_id: str | None = Field(
        description="Notification recipient, or null when no notification is attached."
    )
    event_type: str | None = Field(
        description="Originating event, or null when no notification is attached."
    )
    title: str = Field(description="Canonical notification title.")
    summary: str = Field(default="你有一条新的通知", description="Stable notification summary.")
    content: str | dict[str, Any] | None = Field(
        description="Original notification content, either text or a JSON object."
    )
    status: WorkOrderStatus | None = Field(
        description="Related work-order status, when one exists."
    )
    is_read: bool | None = Field(
        description="Notification read state, or null when no notification is attached."
    )
    read_at: datetime | None = Field(
        description="UTC notification read time, or null when unread or unavailable."
    )
    env: str = Field(description="Environment scope of the work order.")
    can_approve: bool = Field(
        description="Whether the current user may approve or reject this item."
    )
    gmt_created: datetime = Field(
        description="UTC time when the work order was created."
    )
    gmt_modified: datetime = Field(
        description="UTC time when the displayed item was last modified."
    )


class WorkOrderDetailResponse(_UtcResponseModel):
    """Detailed view of one work order."""

    work_order_id: int = Field(description="Identifier of the work order.")
    work_order_no: str = Field(description="Human-readable work-order number.")
    biz_type: str = Field(description="Business type supplied by the business module.")
    biz_id: int | str = Field(description="Identifier of the related business object.")
    event_type: str | None = Field(
        default=None, description="Legacy originating event, when available."
    )
    title: str = Field(description="Canonical display title of the work order.")
    summary: str = Field(default="你有一条新的通知", description="Stable notification summary.")
    content: str | dict[str, Any] | None = Field(
        default=None, description="Original notification content, when available."
    )
    biz_data: dict[str, Any] | None = Field(
        default=None, description="Work-order business JSON object, when available."
    )
    status: WorkOrderStatus = Field(description="Current work-order status.")
    reviewer_user_id: str | None = Field(
        description="Identifier of the reviewer after processing, otherwise null."
    )
    reviewer_user_name: str | None = Field(
        description="Display name of the reviewer after processing, otherwise null."
    )
    review_remark: str | None = Field(
        description="Review comment after processing, otherwise null."
    )
    reviewed_at: datetime | None = Field(
        description="UTC review time after processing, otherwise null."
    )
    can_approve: bool = Field(
        description="Whether the current user may approve or reject this work order."
    )


class NotificationDetailResponse(BaseModel):
    """Detailed view of a notification addressed to the current user."""

    notification_id: int = Field(description="Identifier of the notification.")
    work_order_id: int | None = Field(
        description="Related work-order identifier, when one exists."
    )
    notification_category: NotificationCategory = Field(
        description="Presentation category of the notification."
    )
    event_type: str = Field(description="Legacy event represented by the notification.")
    title: str = Field(description="Canonical display title of the notification.")
    summary: str = Field(default="你有一条新的通知", description="Stable notification summary.")
    content: str | dict[str, Any] | None = Field(
        description="Original notification content, either text or a JSON object."
    )
    is_read: bool = Field(
        description="Whether the recipient has read the notification."
    )
    work_order_status: WorkOrderStatus | None = Field(
        description="Related work-order status, when a work order exists."
    )
    can_approve: bool = Field(
        description="Whether the recipient may review the related work order."
    )
    biz_type: str = Field(description="Business type supplied by the business module.")
    biz_id: str = Field(description="Identifier of the related business object.")


class UnreadCountResponse(BaseModel):
    """Notification and actionable approval badge totals for the current user."""

    unread_count: int = Field(
        description="Number of unread notifications, retained for compatibility."
    )
    pending_approval_count: int = Field(
        description="Number of pending work orders the current user can approve."
    )
    unread_notice_count: int = Field(
        description="Number of unread informational notifications."
    )
    badge_count: int = Field(
        description="Badge total: pending approvals plus unread notices."
    )


class NotificationReadResponse(_UtcResponseModel):
    """Read state returned after one notification is marked read."""

    notification_id: int = Field(description="Identifier of the notification.")
    is_read: bool = Field(description="Whether the notification is now read.")
    read_at: datetime | None = Field(
        description="UTC time when the notification was marked read."
    )


class NotificationsReadAllResponse(BaseModel):
    """Result of marking all current user's notifications read."""

    updated_count: int = Field(
        description="Number of notifications changed from unread to read."
    )
