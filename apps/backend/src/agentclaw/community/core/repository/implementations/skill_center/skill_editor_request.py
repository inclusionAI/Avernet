"""Atomic persistence workflow for Space Skill editor applications."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from injector import inject
from sqlalchemy.orm import Session

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillGrant, SkillSpaceBinding
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderNotFoundError,
    WorkOrderSkillEditorRequestNotAllowedError,
    WorkOrderSkillApplicantAlreadyEditorError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderNotificationDraft,
    WorkOrderReviewResult,
    WorkOrderStatus,
    notification_title_for,
    skill_collaborator_applicant_display,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillEditorRequestRepositoryProtocol,
)
from agentclaw.community.core.skill_center.editor_request_contract import (
    SkillEditorRequestAdmission,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


_AUTO_PROCESSING_STATUS = "PROCESSING"  # WorkOrder AUTO claim; owned by PR #2463.
_SYSTEM_REVIEWER_USER_ID = "SYSTEM"


class SkillEditorRequestRepository(SkillEditorRequestRepositoryProtocol):
    """Skill-owned integration UoW for editor requests and Work Orders."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _new_no() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"WO{stamp}{uuid4().hex[:10].upper()}"

    @staticmethod
    def _lock_editor_approval_policy(
        session: Session, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> SkillSpaceBinding:
        binding = (
            session.query(SkillSpaceBinding)
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.env == env,
            )
            .with_for_update()
            .one_or_none()
        )
        space = (
            session.query(SpaceModel)
            .filter(
                SpaceModel.id == space_id,
                SpaceModel.env == env,
                SpaceModel.deleted_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        skill = (
            session.query(Skill)
            .filter(Skill.id == skill_id, Skill.env == env)
            .with_for_update()
            .one_or_none()
        )
        if binding is None or space is None or skill is None:
            raise SpaceSkillGrantNotFoundError("Space Skill not found")
        if space.space_type != SpaceType.TEAM.value or skill.offline_at is not None:
            raise SpaceSkillGrantForbiddenError(
                "approval policy requires a live Team Space Skill"
            )
        member = (
            session.query(SpaceMemberModel.id)
            .filter(
                SpaceMemberModel.space_id == space_id,
                SpaceMemberModel.user_id == actor_id,
                SpaceMemberModel.status == "ACTIVE",
                SpaceMemberModel.env == env,
            )
            .with_for_update()
            .first()
        )
        owner = (
            session.query(SkillGrant.id)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == actor_id,
                SkillGrant.role == "OWNER",
                SkillGrant.status == "ACTIVE",
                SkillGrant.owner_slot == 1,
                SkillGrant.env == env,
            )
            .with_for_update()
            .first()
        )
        if member is None or owner is None:
            raise SpaceSkillGrantForbiddenError("current Skill Owner role required")
        return binding

    def get_editor_approval_policy(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> bool:
        with self._db.transactional_orm_session() as session:
            binding = self._lock_editor_approval_policy(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
            )
            return bool(binding.auto_approve_editor_requests)

    def update_editor_approval_policy(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        auto_approve_editor_requests: bool,
        env: str,
    ) -> bool:
        with self._db.transactional_orm_session() as session:
            binding = self._lock_editor_approval_policy(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
            )
            binding.auto_approve_editor_requests = auto_approve_editor_requests
            session.flush()
            return bool(binding.auto_approve_editor_requests)

    @staticmethod
    def _qualified_editor_request(
        db: Session,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        env: str,
    ) -> tuple[Skill, str, bool]:
        binding = (
            db.query(SkillSpaceBinding)
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.env == env,
            )
            .with_for_update()
            .one_or_none()
        )
        if binding is None:
            raise WorkOrderNotFoundError("Space Skill not found")
        space = (
            db.query(SpaceModel)
            .filter(
                SpaceModel.id == space_id,
                SpaceModel.env == env,
                SpaceModel.deleted_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        skill = (
            db.query(Skill)
            .filter(Skill.id == skill_id, Skill.env == env)
            .with_for_update()
            .one_or_none()
        )
        if space is None or skill is None:
            raise WorkOrderNotFoundError("Space Skill not found")
        if space.space_type != SpaceType.TEAM.value or skill.offline_at is not None:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "only live Team Space Skills accept editor requests"
            )
        member = (
            db.query(SpaceMemberModel.id)
            .filter(
                SpaceMemberModel.space_id == space_id,
                SpaceMemberModel.user_id == applicant_user_id,
                SpaceMemberModel.status == "ACTIVE",
                SpaceMemberModel.env == env,
            )
            .with_for_update()
            .first()
        )
        if member is None:
            raise WorkOrderAccessDeniedError(
                "applicant must be an active Team Space member"
            )
        existing_grant = (
            db.query(SkillGrant.id)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == applicant_user_id,
                SkillGrant.status == "ACTIVE",
                SkillGrant.env == env,
            )
            .first()
        )
        if existing_grant is not None:
            raise WorkOrderSkillApplicantAlreadyEditorError(
                "applicant already has Skill editor access"
            )
        owner = (
            db.query(SkillGrant.user_id)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.role == "OWNER",
                SkillGrant.status == "ACTIVE",
                SkillGrant.owner_slot == 1,
                SkillGrant.env == env,
            )
            .first()
        )
        if owner is None:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "Skill has no active Owner"
            )
        pending = (
            db.query(WorkOrderModel.id)
            .filter(
                WorkOrderModel.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value,
                WorkOrderModel.biz_id == str(skill_id),
                WorkOrderModel.applicant_user_id == applicant_user_id,
                WorkOrderModel.status.in_(
                    (WorkOrderStatus.PENDING.value, WorkOrderStatus.PROCESSING.value)
                ),
                WorkOrderModel.env == env,
            )
            .first()
        )
        if pending is not None:
            raise WorkOrderAlreadyPendingError("pending request already exists")
        return skill, owner[0], bool(binding.auto_approve_editor_requests)

    def inspect_editor_request(
        self, *, space_id: int, skill_id: int, applicant_user_id: str, env: str
    ) -> SkillEditorRequestAdmission:
        with self._db.transactional_orm_session() as db:
            skill, _owner_id, auto_approve = self._qualified_editor_request(
                db,
                space_id=space_id,
                skill_id=skill_id,
                applicant_user_id=applicant_user_id,
                env=env,
            )
            return SkillEditorRequestAdmission(
                auto_approve=auto_approve, skill_name=skill.name
            )

    @staticmethod
    def _auto_order_identity(*, biz_id: str, biz_data: str | None) -> tuple[int, int]:
        try:
            data = json.loads(biz_data or "{}")
            space_id = int(data["space_id"])
            skill_id = int(data["skill_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "work-order Skill identity is invalid"
            ) from exc
        if str(skill_id) != biz_id:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "work-order Skill identity is inconsistent"
            )
        return space_id, skill_id

    def admit_auto_skill_editor_request(
        self,
        *,
        session: Session,
        biz_id: str,
        biz_data: str | None,
        applicant_user_id: str,
        env: str,
    ) -> None:
        if not applicant_user_id:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "Skill editor applicant is required"
            )
        space_id, skill_id = self._auto_order_identity(biz_id=biz_id, biz_data=biz_data)
        _, _, auto_approve = self._qualified_editor_request(
            session,
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id=applicant_user_id,
            env=env,
        )
        if not auto_approve:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "automatic Skill editor approval is disabled"
            )

    def create_skill_editor_request(
        self,
        *,
        space_id: int,
        skill_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str,
        env: str,
    ):
        with self._db.transactional_orm_session() as db:
            skill, owner_id, auto_approve = self._qualified_editor_request(
                db,
                space_id=space_id,
                skill_id=skill_id,
                applicant_user_id=applicant_user_id,
                env=env,
            )
            if auto_approve:
                raise WorkOrderSkillEditorRequestNotAllowedError(
                    "automatic Skill editor requests must use WorkOrder AUTO"
                )
            title = WorkOrderMessageTitle.SKILL_COLLABORATOR_PENDING.value
            content = WorkOrderMessageContent.SKILL_COLLABORATOR_PENDING.value.format(
                applicant_display=skill_collaborator_applicant_display(
                    applicant_user_id=applicant_user_id,
                    applicant_name=applicant_name,
                ),
                skill_name=skill.name,
            )
            order = WorkOrderModel(
                work_order_no=self._new_no(),
                biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
                biz_id=str(skill_id),
                biz_data=json.dumps(
                    {
                        "space_id": space_id,
                        "skill_id": skill_id,
                        "skill_name": skill.name,
                    },
                    ensure_ascii=False,
                ),
                applicant_user_id=applicant_user_id,
                apply_reason=apply_reason,
                status=WorkOrderStatus.PENDING.value,
                env=env,
            )
            db.add(order)
            db.flush()
            db.add(
                WorkOrderApproverModel(
                    work_order_id=order.id,
                    approver_user_id=owner_id,
                    status=WorkOrderApproverStatus.PENDING.value,
                    env=env,
                )
            )
            db.add(
                WorkOrderNotificationModel(
                    work_order_id=order.id,
                    recipient_user_id=owner_id,
                    notification_category=NotificationCategory.APPROVAL.value,
                    event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
                    biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
                    biz_id=str(skill_id),
                    title=notification_title_for(
                        WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value, title
                    ),
                    content=content,
                    env=env,
                )
            )
            db.flush()
            db.refresh(order)
            return order.to_record()

    def review_skill_editor_request(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ) -> WorkOrderReviewResult:
        reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._db.transactional_orm_session() as db:
            order = (
                db.query(WorkOrderModel)
                .filter(
                    WorkOrderModel.id == work_order_id,
                    WorkOrderModel.biz_type
                    == WorkOrderBizType.SKILL_COLLABORATOR.value,
                    WorkOrderModel.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("Skill editor work order not found")
            if order.status != WorkOrderStatus.PENDING.value:
                raise WorkOrderAlreadyProcessedError("work order already processed")
            try:
                data = json.loads(order.biz_data or "{}")
                space_id = int(data["space_id"])
                skill_id = int(data["skill_id"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise WorkOrderSkillEditorRequestNotAllowedError(
                    "work-order Skill identity is invalid"
                ) from exc
            if str(skill_id) != order.biz_id:
                raise WorkOrderSkillEditorRequestNotAllowedError(
                    "work-order Skill identity is inconsistent"
                )

            binding = (
                db.query(SkillSpaceBinding)
                .filter(
                    SkillSpaceBinding.space_id == space_id,
                    SkillSpaceBinding.skill_id == skill_id,
                    SkillSpaceBinding.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            skill = (
                db.query(Skill.id)
                .filter(
                    Skill.id == skill_id,
                    Skill.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            space = (
                db.query(SpaceModel.id)
                .filter(
                    SpaceModel.id == space_id,
                    SpaceModel.space_type == SpaceType.TEAM.value,
                    SpaceModel.env == env,
                    SpaceModel.deleted_at.is_(None),
                )
                .with_for_update()
                .one_or_none()
            )
            if binding is None or skill is None or space is None:
                raise WorkOrderNotFoundError("work-order Space Skill not found")
            owner = (
                db.query(SkillGrant)
                .filter(
                    SkillGrant.skill_id == skill_id,
                    SkillGrant.role == "OWNER",
                    SkillGrant.status == "ACTIVE",
                    SkillGrant.owner_slot == 1,
                    SkillGrant.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if owner is None or owner.user_id != reviewer_user_id:
                raise WorkOrderAccessDeniedError("current Skill Owner role required")
            approver = (
                db.query(WorkOrderApproverModel)
                .filter(
                    WorkOrderApproverModel.work_order_id == work_order_id,
                    WorkOrderApproverModel.approver_user_id == reviewer_user_id,
                    WorkOrderApproverModel.status
                    == WorkOrderApproverStatus.PENDING.value,
                    WorkOrderApproverModel.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if approver is None:
                raise WorkOrderAccessDeniedError("current user is not an approver")
            if target_status is WorkOrderStatus.APPROVED:
                member = (
                    db.query(SpaceMemberModel.id)
                    .filter(
                        SpaceMemberModel.space_id == space_id,
                        SpaceMemberModel.user_id == order.applicant_user_id,
                        SpaceMemberModel.status == "ACTIVE",
                        SpaceMemberModel.env == env,
                    )
                    .first()
                )
                if member is None:
                    raise WorkOrderSkillEditorRequestNotAllowedError(
                        "applicant is no longer an active Team Space member"
                    )
                grant = (
                    db.query(SkillGrant)
                    .filter(
                        SkillGrant.skill_id == skill_id,
                        SkillGrant.user_id == order.applicant_user_id,
                        SkillGrant.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if (
                    grant is not None
                    and grant.role == "OWNER"
                    and grant.status == "ACTIVE"
                ):
                    raise WorkOrderSkillApplicantAlreadyEditorError(
                        "applicant is now the Skill Owner"
                    )
                if grant is None:
                    grant = SkillGrant(
                        skill_id=skill_id,
                        user_id=order.applicant_user_id,
                        role="MANAGER",
                        status="ACTIVE",
                        owner_slot=None,
                        granted_by=reviewer_user_id,
                        grant_reason=order.apply_reason,
                        env=env,
                    )
                    db.add(grant)
                else:
                    grant.role = "MANAGER"
                    grant.status = "ACTIVE"
                    grant.owner_slot = None
                    grant.granted_by = reviewer_user_id
                    grant.grant_reason = order.apply_reason
                    grant.revoked_at = None
                    grant.revoked_by = None

            order.status = target_status.value
            order.reviewer_user_id = reviewer_user_id
            order.review_remark = review_remark
            order.reviewed_at = reviewed_at
            order.gmt_modified = reviewed_at
            approver.status = target_status.value
            approver.review_remark = review_remark
            approver.reviewed_at = reviewed_at
            approver.gmt_modified = reviewed_at
            db.add(
                WorkOrderNotificationModel(
                    work_order_id=work_order_id,
                    recipient_user_id=notification.recipient_user_id,
                    notification_category=notification.notification_category.value,
                    event_type=WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED.value,
                    biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
                    biz_id=str(skill_id),
                    title=notification_title_for(
                        WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED.value,
                        notification.title,
                    ),
                    content=json.dumps(notification.content, ensure_ascii=False),
                    env=env,
                )
            )
            db.flush()
            return WorkOrderReviewResult(
                work_order_id=work_order_id,
                status=target_status,
                decision=WorkOrderDecision(target_status.value),
                reviewer_user_id=reviewer_user_id,
                review_remark=review_remark,
                reviewed_at=reviewed_at,
            )

    def apply_auto_skill_editor_request(
        self, *, session: Session, work_order_id: int, env: str
    ) -> None:
        """Apply only the Skill Grant inside WorkOrder's open transaction.

        WorkOrder verifies persisted AUTO mode, owns claim/finalization and
        result notification, and commits or rolls back this same session.
        """
        order = (
            session.query(WorkOrderModel)
            .filter(
                WorkOrderModel.id == work_order_id,
                WorkOrderModel.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value,
                WorkOrderModel.env == env,
            )
            .with_for_update()
            .one_or_none()
        )
        if order is None:
            raise WorkOrderNotFoundError("Skill editor work order not found")
        if order.status != _AUTO_PROCESSING_STATUS:
            raise WorkOrderAlreadyProcessedError(
                "AUTO Skill order must be claimed before approval"
            )
        if not order.applicant_user_id:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "Skill editor applicant is required"
            )
        space_id, skill_id = self._auto_order_identity(
            biz_id=order.biz_id, biz_data=order.biz_data
        )
        if (
            session.query(WorkOrderApproverModel.id)
            .filter(
                WorkOrderApproverModel.work_order_id == work_order_id,
                WorkOrderApproverModel.env == env,
            )
            .first()
            is not None
        ):
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "AUTO Skill order must not have a human approver"
            )
        binding = (
            session.query(SkillSpaceBinding)
            .filter(
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.env == env,
            )
            .with_for_update()
            .one_or_none()
        )
        skill = (
            session.query(Skill)
            .filter(Skill.id == skill_id, Skill.env == env)
            .with_for_update()
            .one_or_none()
        )
        space = (
            session.query(SpaceModel.id)
            .filter(
                SpaceModel.id == space_id,
                SpaceModel.space_type == SpaceType.TEAM.value,
                SpaceModel.env == env,
                SpaceModel.deleted_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if binding is None or skill is None or space is None:
            raise WorkOrderNotFoundError("work-order Space Skill not found")
        if skill.offline_at is not None:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "offline Skill does not accept editor approvals"
            )
        if not binding.auto_approve_editor_requests:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "automatic Skill editor approval is disabled"
            )
        owner = (
            session.query(SkillGrant.id)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.role == "OWNER",
                SkillGrant.status == "ACTIVE",
                SkillGrant.owner_slot == 1,
                SkillGrant.env == env,
            )
            .with_for_update()
            .first()
        )
        if owner is None:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "Skill has no active Owner"
            )
        member = (
            session.query(SpaceMemberModel.id)
            .filter(
                SpaceMemberModel.space_id == space_id,
                SpaceMemberModel.user_id == order.applicant_user_id,
                SpaceMemberModel.status == "ACTIVE",
                SpaceMemberModel.env == env,
            )
            .with_for_update()
            .first()
        )
        if member is None:
            raise WorkOrderSkillEditorRequestNotAllowedError(
                "applicant is no longer an active Team Space member"
            )
        grant = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == order.applicant_user_id,
                SkillGrant.env == env,
            )
            .with_for_update()
            .one_or_none()
        )
        if grant is not None and grant.role == "OWNER" and grant.status == "ACTIVE":
            raise WorkOrderSkillApplicantAlreadyEditorError(
                "applicant is now the Skill Owner"
            )
        if grant is None:
            session.add(
                SkillGrant(
                    skill_id=skill_id,
                    user_id=order.applicant_user_id,
                    role="MANAGER",
                    status="ACTIVE",
                    owner_slot=None,
                    granted_by=_SYSTEM_REVIEWER_USER_ID,
                    grant_reason=order.apply_reason,
                    env=env,
                )
            )
        elif grant.status != "ACTIVE":
            grant.role = "MANAGER"
            grant.status = "ACTIVE"
            grant.owner_slot = None
            grant.granted_by = _SYSTEM_REVIEWER_USER_ID
            grant.grant_reason = order.apply_reason
            grant.revoked_at = None
            grant.revoked_by = None
        session.flush()

    @staticmethod
    def reroute_pending_reviewer(
        session,
        *,
        skill_id: int,
        previous_owner_user_id: str,
        new_owner_user_id: str,
        env: str,
    ) -> None:
        """Move pending Skill reviews with the authoritative Owner Grant."""
        pending_ids = [
            work_order_id
            for (work_order_id,) in session.query(WorkOrderModel.id)
            .filter(
                WorkOrderModel.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value,
                WorkOrderModel.biz_id == str(skill_id),
                WorkOrderModel.status == WorkOrderStatus.PENDING.value,
                WorkOrderModel.env == env,
            )
            .with_for_update()
            .all()
        ]
        if not pending_ids:
            return
        session.query(WorkOrderApproverModel).filter(
            WorkOrderApproverModel.work_order_id.in_(pending_ids),
            WorkOrderApproverModel.approver_user_id == previous_owner_user_id,
            WorkOrderApproverModel.status == WorkOrderApproverStatus.PENDING.value,
            WorkOrderApproverModel.env == env,
        ).update(
            {WorkOrderApproverModel.status: WorkOrderApproverStatus.CANCELLED.value},
            synchronize_session=False,
        )
        for work_order_id in pending_ids:
            new_approver = (
                session.query(WorkOrderApproverModel)
                .filter(
                    WorkOrderApproverModel.work_order_id == work_order_id,
                    WorkOrderApproverModel.approver_user_id == new_owner_user_id,
                    WorkOrderApproverModel.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if new_approver is None:
                session.add(
                    WorkOrderApproverModel(
                        work_order_id=work_order_id,
                        approver_user_id=new_owner_user_id,
                        status=WorkOrderApproverStatus.PENDING.value,
                        env=env,
                    )
                )
            else:
                new_approver.status = WorkOrderApproverStatus.PENDING.value
                new_approver.review_remark = None
                new_approver.reviewed_at = None
            session.query(WorkOrderNotificationModel).filter(
                WorkOrderNotificationModel.work_order_id == work_order_id,
                WorkOrderNotificationModel.recipient_user_id == previous_owner_user_id,
                WorkOrderNotificationModel.notification_category
                == NotificationCategory.APPROVAL.value,
                WorkOrderNotificationModel.env == env,
            ).update(
                {WorkOrderNotificationModel.recipient_user_id: new_owner_user_id},
                synchronize_session=False,
            )
        session.flush()


__all__ = ["SkillEditorRequestRepository"]
