"""Persistence contract for work orders and notifications."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable


if TYPE_CHECKING:
    from agentclaw.community.core.work_orders.models import (
        WorkOrderApprovalContext,
        WorkOrderDetail,
        WorkOrderItemType,
        WorkOrderListItem,
        WorkOrderNotificationDetail,
        WorkOrderNotificationBadgeSummary,
        WorkOrderNotificationDraft,
        WorkOrderNotificationRecord,
        WorkOrderQueryType,
        WorkOrderRecord,
        WorkOrderReviewResult,
        WorkOrderStatus,
        WorkOrderDecision,
        WorkOrderEventCreatedResult,
        NotificationCategory,
        WorkOrderApprovalMode,
    )


@runtime_checkable
class WorkOrderRepositoryProtocol(Protocol):
    @abstractmethod
    def create_work_order_event(
        self,
        *,
        event_category: NotificationCategory,
        approval_mode: WorkOrderApprovalMode | None = None,
        biz_type: str,
        biz_id: str,
        event_type: str,
        applicant_user_id: str | None,
        approver_user_ids: list[str],
        recipient_user_ids: list[str],
        title: str,
        content: str | None,
        apply_reason: str | None,
        biz_data: str | None,
        env: str,
        callback_source_event_type: str | None = None,
    ) -> WorkOrderEventCreatedResult:
        """Persist normalized service input; AUTO requires approver_user_ids.

        AUTO is created PROCESSING without approver rows or initial notices.
        Completion receives the same approver-derived recipients separately.
        """
        ...

    @abstractmethod
    def create_work_order(
        self,
        *,
        biz_type: str,
        biz_id: str,
        applicant_user_id: str,
        apply_reason: str | None,
        biz_data: str | None,
        approver_user_ids: list[str],
        notification_recipient_user_ids: list[str],
        env: str,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def get_approval_context(
        self, *, work_order_id: int, reviewer_user_id: str, env: str
    ) -> WorkOrderApprovalContext: ...

    @abstractmethod
    def claim_auto_approval(
        self, *, work_order_id: int, reviewer_user_id: str, env: str
    ) -> None: ...

    @abstractmethod
    def complete_auto_approval(
        self,
        *,
        work_order_id: int,
        recipient_user_ids: list[str],
        source_event_type: str,
        env: str,
    ) -> list[int]:
        """Commit the business write, APPROVED state, and result notices together."""
        ...

    @abstractmethod
    def fail_auto_approval(
        self,
        *,
        work_order_id: int,
        recipient_user_ids: list[str],
        source_event_type: str,
        review_remark: str,
        env: str,
    ) -> list[int]:
        """Commit FAILED state and result notices together."""
        ...

    @abstractmethod
    def apply_auto_skill_editor_request(
        self,
        *,
        work_order_id: int,
        recipient_user_ids: list[str],
        source_event_type: str,
        env: str,
    ) -> list[int]:
        """Atomically grant Skill access and notify approver-derived recipients."""
        ...

    @abstractmethod
    def process_approval(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        decision: WorkOrderDecision,
        review_remark: str | None,
        env: str,
        source_event_type: str | None = None,
    ) -> WorkOrderReviewResult: ...
    @abstractmethod
    def create_space_join_request(
        self,
        *,
        space_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str | None,
        env: str,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def get_bot_editor_request_policy(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        env: str,
    ) -> bool:
        """Read the Team Space Bot policy; only the addressed Owner may read it."""
        ...

    @abstractmethod
    def update_bot_editor_request_policy(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        auto_approve: bool,
        env: str,
    ) -> bool:
        """Owner-only atomic ext merge, serialized with new requests. No retroactive approvals."""
        ...

    @abstractmethod
    def create_bot_editor_request(
        self,
        *,
        bot_pk: int,
        bot_id: str,
        bot_name: str,
        owner_id: str,
        space_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str,
        env: str,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def create_skill_editor_request(
        self,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str,
        env: str,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def list_items(
        self,
        *,
        actor_id: str,
        env: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
        biz_type: str | None = None,
        biz_id: str | None = None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[WorkOrderListItem]]:
        """List inbox entries or unique initiated orders.

        Terminal order notices are processed regardless of read state. Initiated
        ALL/APPROVAL returns orders without notifications; NOTICE stays a
        recipient-scoped notification view. Badge counts remain read-sensitive.
        """
        ...

    @abstractmethod
    def get_detail(
        self, *, work_order_id: int, actor_id: str, env: str
    ) -> WorkOrderDetail | None: ...

    @abstractmethod
    def review_space_join(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        applicant_user_name: str | None,
        env: str,
    ) -> WorkOrderReviewResult: ...

    @abstractmethod
    def review_bot_editor_request(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ) -> WorkOrderReviewResult: ...

    @abstractmethod
    def review_skill_editor_request(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ) -> WorkOrderReviewResult: ...

    @abstractmethod
    def get_notification(
        self,
        *,
        notification_id: int,
        recipient_user_id: str,
        env: str,
        mark_read: bool,
    ) -> WorkOrderNotificationDetail | None: ...

    @abstractmethod
    def count_unread(self, *, recipient_user_id: str, env: str) -> int: ...

    @abstractmethod
    def get_notification_badge_summary(
        self, *, recipient_user_id: str, env: str
    ) -> WorkOrderNotificationBadgeSummary: ...

    @abstractmethod
    def mark_notification_read(
        self, *, notification_id: int, recipient_user_id: str, env: str
    ) -> WorkOrderNotificationRecord | None: ...

    @abstractmethod
    def mark_all_notifications_read(
        self, *, recipient_user_id: str, env: str
    ) -> int: ...
