"""Skill editor request persistence contract, separate from the large Skill surface."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.editor_request_contract import (
        SkillEditorRequestAdmission,
    )
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
    def get_editor_approval_policy(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> bool: ...

    @abstractmethod
    def update_editor_approval_policy(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        auto_approve_editor_requests: bool,
        env: str,
    ) -> bool: ...

    @abstractmethod
    def inspect_editor_request(
        self, *, space_id: int, skill_id: int, applicant_user_id: str, env: str
    ) -> SkillEditorRequestAdmission: ...

    @abstractmethod
    def admit_auto_skill_editor_request(
        self,
        *,
        session: Session,
        biz_id: str,
        biz_data: str | None,
        applicant_user_id: str,
        env: str,
    ) -> None:
        """Lock the Skill Space binding and recheck before AUTO order insertion.

        The caller owns ``session`` and must keep its transaction open through
        the WorkOrder insert and commit. This method does not commit or close it.
        """
        ...

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
