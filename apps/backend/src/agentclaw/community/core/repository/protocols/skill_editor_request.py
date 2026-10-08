"""Skill editor request persistence contract, separate from the large Skill surface."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from agentclaw.community.core.work_orders.models import (
        WorkOrderNotificationDraft,
        WorkOrderRecord,
        WorkOrderReviewResult,
        WorkOrderStatus,
    )


@runtime_checkable
class SkillEditorRequestRepositoryProtocol(Protocol):
    """Atomic Skill-owned seam spanning editor requests and their Work Orders."""

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
    def apply_auto_skill_editor_request(
        self, *, session: Session, work_order_id: int, env: str
    ) -> None:
        """Validate and write only the Grant in WorkOrder's open transaction."""
        ...

    @staticmethod
    @abstractmethod
    def reroute_pending_reviewer(
        session: Any,
        *,
        skill_id: int,
        previous_owner_user_id: str,
        new_owner_user_id: str,
        env: str,
    ) -> None: ...
