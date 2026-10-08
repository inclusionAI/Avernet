"""Skill editor request persistence contract, separate from the large Skill surface."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

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
    def approve_auto_skill_editor_request(
        self, *, work_order_id: int, env: str
    ) -> WorkOrderReviewResult: ...

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
