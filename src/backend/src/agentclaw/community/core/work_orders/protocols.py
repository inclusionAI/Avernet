"""Core-facing contracts for work-order integrations."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.work_orders.models import (
        NotificationCategory,
        WorkOrderEventCreatedResult,
    )


@runtime_checkable
class WorkOrderEventServiceProtocol(Protocol):
    """Minimal work-order contract required by core domain services."""

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
