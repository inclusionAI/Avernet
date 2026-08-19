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
    )


@runtime_checkable
class WorkOrderServiceProtocol(Protocol):
    @abstractmethod
    def create_space_join_request(
        self, *, space_id: int, applicant_user_id: str, reason: str
    ) -> WorkOrderRecord: ...

    @abstractmethod
    def list_items(
        self,
        *,
        actor_id: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
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
