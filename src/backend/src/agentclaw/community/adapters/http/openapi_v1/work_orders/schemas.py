"""HTTP request and response schemas for work orders and notifications."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


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

    __descriptions__ = {"SPACE_JOIN": "A request to join a Space."}


class NotificationCategory(_DocumentedEnum):
    """How a notification is presented to its recipient."""

    APPROVAL = "APPROVAL"
    NOTICE = "NOTICE"

    __descriptions__ = {
        "APPROVAL": "Requires a review decision from the recipient.",
        "NOTICE": "Reports an event and requires no review decision.",
    }


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

    __descriptions__ = {
        "SPACE_JOIN_APPLIED": "A user submitted a request to join a Space.",
        "SPACE_JOIN_REVIEWED": "A Space join request received a review decision.",
        "SPACE_MEMBER_ADDED": "A user was directly added to a Space.",
        "BOT_COLLABORATOR_APPLIED": "A bot collaborator request was submitted.",
        "BOT_COLLABORATOR_REVIEWED": "A bot collaborator request was reviewed.",
        "BOT_MEMBER_ADDED": "A member was directly added to a bot.",
        "HUMAN2BOT_FRIEND_APPLIED": "A user-to-bot friend request was submitted.",
        "HUMAN2BOT_FRIEND_REVIEWED": "A user-to-bot friend request was reviewed.",
        "BOT2BOT_FRIEND_APPLIED": "A bot-to-bot friend request was submitted.",
        "BOT2BOT_FRIEND_REVIEWED": "A bot-to-bot friend request was reviewed.",
        "HUMAN2BOT_PUBLIC_ORDER_CREATED": "A user created a public order for a bot.",
        "HUMAN2BOT_PUBLIC_ORDER_COMPLETED": "A user's public bot order completed.",
        "BOT2BOT_PUBLIC_ORDER_CREATED": "A bot created a public order for another bot.",
        "BOT2BOT_PUBLIC_ORDER_COMPLETED": "A bot-to-bot public order completed.",
    }


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
    """Request for joining a Space."""

    reason: str = Field(
        min_length=1, max_length=512, description="Reason for requesting membership."
    )


class SpaceJoinRequestCreated(BaseModel):
    """Work-order identity returned for a new Space join request."""

    work_order_id: int = Field(description="Identifier of the created work order.")
    work_order_no: str = Field(description="Human-readable work-order number.")
    status: WorkOrderStatus = Field(description="Initial work-order status.")


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
    reviewer_user_id: str = Field(description="Identifier of the reviewer.")
    review_remark: str | None = Field(
        description="Reviewer comment, or null for approval without a comment."
    )
    reviewed_at: datetime = Field(
        description="UTC time when the review decision was recorded."
    )


class WorkOrderListItem(_UtcResponseModel):
    """Approval or notification item in a user's work-order inbox."""

    item_id: str = Field(description="Stable identifier of this combined list item.")
    item_type: WorkOrderItemType = Field(description="Category of the list item.")
    work_order_id: int = Field(description="Identifier of the related work order.")
    work_order_no: str = Field(description="Human-readable work-order number.")
    notification_id: int | None = Field(
        description="Related notification identifier, or null for approval-only items."
    )
    notification_category: NotificationCategory | None = Field(
        description="Notification category, or null when no notification is attached."
    )
    biz_type: WorkOrderBizType = Field(description="Business object type.")
    biz_id: str = Field(description="Identifier of the related business object.")
    applicant_user_id: str = Field(description="Identifier of the applicant.")
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
    event_type: WorkOrderEventType | None = Field(
        description="Originating event, or null when no notification is attached."
    )
    title: str | None = Field(description="Notification title, when available.")
    content: str | None = Field(description="Notification content, when available.")
    status: WorkOrderStatus = Field(description="Current work-order status.")
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


class WorkOrderDetailContent(BaseModel):
    """Business details for a Space join work order."""

    space_id: int = Field(description="Identifier of the requested Space.")
    space_name: str = Field(description="Display name of the requested Space.")
    applicant_user_id: str = Field(description="Identifier of the applicant.")
    applicant_name: str = Field(description="Display name of the applicant.")
    reason: str | None = Field(description="Applicant's reason, when supplied.")


class WorkOrderDetailResponse(_UtcResponseModel):
    """Detailed view of one work order."""

    work_order_id: int = Field(description="Identifier of the work order.")
    work_order_no: str = Field(description="Human-readable work-order number.")
    biz_type: WorkOrderBizType = Field(description="Business object type.")
    biz_id: int = Field(description="Identifier of the related Space.")
    event_type: WorkOrderEventType = Field(
        description="Event that created the work order."
    )
    title: str = Field(description="Display title of the work order.")
    content: WorkOrderDetailContent = Field(description="Space join request details.")
    status: WorkOrderStatus = Field(description="Current work-order status.")
    reviewer_user_id: str | None = Field(
        description="Identifier of the reviewer after processing, otherwise null."
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
    event_type: WorkOrderEventType = Field(
        description="Business event represented by the notification."
    )
    title: str = Field(description="Display title of the notification.")
    content: str | None = Field(
        description="Notification message content, when present."
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
    biz_type: WorkOrderBizType = Field(description="Business object type.")
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
