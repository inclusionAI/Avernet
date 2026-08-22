"""Persistence contract for work orders and notifications."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.work_orders.models import (
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
    )


@runtime_checkable
class WorkOrderRepositoryProtocol(Protocol):
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
    def process_approval(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        decision: WorkOrderDecision,
        review_remark: str | None,
        env: str,
    ) -> WorkOrderReviewResult: ...
    @abstractmethod
    def create_space_join_request(
        self,
        *,
        space_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str,
        env: str,
    ) -> WorkOrderRecord: ...

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
    ) -> tuple[int, list[WorkOrderListItem]]: ...

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
