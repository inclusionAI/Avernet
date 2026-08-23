"""Service API contracts for work orders and recipient notifications."""

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
        WorkOrderNotificationRecord,
        WorkOrderQueryType,
        WorkOrderRecord,
        WorkOrderReviewResult,
        WorkOrderDecision,
        WorkOrderEventCreatedResult,
        NotificationCategory,
    )


@runtime_checkable
class WorkOrderServiceProtocol(Protocol):
    @abstractmethod
    def create_work_order_event(
        self,
        *,
        event_category: NotificationCategory,
        biz_type: str,
        biz_id: str,
        event_type: str,
        applicant_user_id: str | None,
        approver_user_ids: list[str],
        recipient_user_ids: list[str],
        title: str,
        content: dict[str, object] | None,
        apply_reason: str | None,
        biz_data: dict[str, object] | None,
        actor_id: str,
    ) -> WorkOrderEventCreatedResult: ...

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
        notification_recipient_user_ids: list[str] | None = None,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def process_approval(
        self,
        *,
        work_order_id: int,
        actor_id: str,
        decision: WorkOrderDecision,
        review_remark: str | None,
    ) -> WorkOrderReviewResult: ...
    @abstractmethod
    def create_space_join_request(
        self, *, space_id: int, applicant_user_id: str, reason: str | None
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def create_bot_editor_request(
        self,
        *,
        bot_id: str,
        owner_id: str,
        applicant_user_id: str,
        reason: str,
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def list_items(
        self,
        *,
        actor_id: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
        biz_type: str | None = None,
        biz_id: str | None = None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[WorkOrderListItem]]: ...

    @abstractmethod
    def get_detail(self, *, work_order_id: int, actor_id: str) -> WorkOrderDetail: ...

    @abstractmethod
    def approve(
        self, *, work_order_id: int, actor_id: str, review_remark: str | None
    ) -> WorkOrderReviewResult: ...

    @abstractmethod
    def reject(
        self, *, work_order_id: int, actor_id: str, review_remark: str | None
    ) -> WorkOrderReviewResult: ...


@runtime_checkable
class WorkOrderNotificationServiceProtocol(Protocol):
    @abstractmethod
    def get_detail(
        self, *, notification_id: int, actor_id: str
    ) -> WorkOrderNotificationDetail: ...

    @abstractmethod
    def unread_count(self, *, actor_id: str) -> int: ...

    @abstractmethod
    def badge_summary(self, *, actor_id: str) -> WorkOrderNotificationBadgeSummary: ...

    @abstractmethod
    def mark_read(
        self, *, notification_id: int, actor_id: str
    ) -> WorkOrderNotificationRecord: ...

    @abstractmethod
    def mark_all_read(self, *, actor_id: str) -> int: ...
