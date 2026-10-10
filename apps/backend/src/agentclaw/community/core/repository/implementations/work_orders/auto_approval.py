"""Transactional repository operations for automatic work-order approval."""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyProcessedError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    SYSTEM_REVIEWER_USER_ID,
    WorkOrderApprovalContext,
    WorkOrderApprovalMode,
    WorkOrderApproverRecord,
    WorkOrderBizType,
    WorkOrderStatus,
    notification_title_for,
    reviewed_event_type_for,
)
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel


class _AutoApprovalWorkOrderRepository:
    """Provide AUTO-only state transitions and business persistence helpers."""

    def claim_auto_approval(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str = SYSTEM_REVIEWER_USER_ID,
        env: str,
    ) -> None:
        """Claim a legacy AUTO row created PENDING before atomic claiming."""
        with self._db.transactional_orm_session() as db:
            updated = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.env == env,
                    self._WorkOrder.approval_mode == WorkOrderApprovalMode.AUTO.value,
                    self._WorkOrder.status == WorkOrderStatus.PENDING.value,
                    self._WorkOrder.reviewer_user_id.is_(None),
                )
                .update(
                    {self._WorkOrder.status: WorkOrderStatus.PROCESSING.value},
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise WorkOrderAlreadyProcessedError("work order already processed")

    def get_approval_context(
        self, *, work_order_id: int, reviewer_user_id: str, env: str
    ) -> WorkOrderApprovalContext:
        with self._db.orm_session() as db:
            order = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.env == env,
                )
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("work order not found")
            approver_record = None
            if order.approval_mode == WorkOrderApprovalMode.AUTO.value:
                if (
                    reviewer_user_id != SYSTEM_REVIEWER_USER_ID
                    or order.status != WorkOrderStatus.PROCESSING.value
                ):
                    raise WorkOrderAccessDeniedError("invalid AUTO approval context")
            else:
                if order.approval_mode not in (
                    None,
                    WorkOrderApprovalMode.MANUAL.value,
                ):
                    raise WorkOrderAccessDeniedError("invalid approval mode")
                approver = (
                    db.query(self._Approver)
                    .filter(
                        self._Approver.work_order_id == work_order_id,
                        self._Approver.approver_user_id == reviewer_user_id,
                        self._Approver.env == env,
                    )
                    .one_or_none()
                )
                if approver is None:
                    raise WorkOrderAccessDeniedError("current user is not an approver")
                approver_record = WorkOrderApproverRecord(
                    id=approver.id,
                    work_order_id=approver.work_order_id,
                    approver_user_id=approver.approver_user_id,
                    status=approver.status,
                    review_remark=approver.review_remark,
                    reviewed_at=approver.reviewed_at,
                    env=approver.env,
                    gmt_created=approver.gmt_created,
                    gmt_modified=approver.gmt_modified,
                )
            source_event = (
                db.query(self._Notification.event_type)
                .filter(
                    self._Notification.work_order_id == work_order_id,
                    self._Notification.notification_category
                    == NotificationCategory.APPROVAL.value,
                    self._Notification.env == env,
                )
                .order_by(self._Notification.id.asc())
                .first()
            )
            return WorkOrderApprovalContext(
                work_order=order.to_record(),
                approver=approver_record,
                source_event_type=source_event[0] if source_event is not None else None,
            )

    def apply_auto_skill_editor_request(
        self,
        *,
        work_order_id: int,
        recipient_user_ids: list[str],
        source_event_type: str,
        env: str,
    ) -> list[int]:
        """Atomically grant Skill access and complete its AUTO work order."""
        with self._db.transactional_orm_session() as db:
            order = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("AUTO Skill work order not found")
            if (
                order.biz_type != WorkOrderBizType.SKILL_COLLABORATOR.value
                or order.approval_mode != WorkOrderApprovalMode.AUTO.value
            ):
                raise WorkOrderAccessDeniedError(
                    "work order is not an AUTO Skill request"
                )
            if order.status != WorkOrderStatus.PROCESSING.value:
                raise WorkOrderAlreadyProcessedError(
                    "AUTO Skill work order is not processing"
                )

            self._skill_editor.apply_auto_skill_editor_request(
                session=db, work_order_id=work_order_id, env=env
            )

            return self._finish_auto_approval_in_session(
                db,
                order=order,
                recipient_user_ids=recipient_user_ids,
                source_event_type=source_event_type,
                env=env,
            )

    def complete_auto_approval(
        self,
        *,
        work_order_id: int,
        recipient_user_ids: list[str],
        source_event_type: str,
        env: str,
    ) -> list[int]:
        """Commit the non-Skill business effect, approval, and notices together."""
        with self._db.transactional_orm_session() as db:
            order = (
                db.query(self._WorkOrder)
                .filter(self._WorkOrder.id == work_order_id, self._WorkOrder.env == env)
                .with_for_update()
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("AUTO work order not found")
            if (
                order.approval_mode != WorkOrderApprovalMode.AUTO.value
                or order.status != WorkOrderStatus.PROCESSING.value
            ):
                raise WorkOrderAlreadyProcessedError("work order is not processing")
            if order.biz_type == WorkOrderBizType.SPACE_JOIN.value:
                self._apply_auto_space_join(db, order=order, env=env)
            elif order.biz_type == WorkOrderBizType.BOT_COLLABORATOR.value:
                self._bot_editor.apply_auto_bot_editor_request(
                    session=db, order=order, env=env
                )
            elif order.biz_type != WorkOrderBizType.BOT_FRIEND.value:
                raise WorkOrderAccessDeniedError(
                    "AUTO business effect is not supported for this work order"
                )
            return self._finish_auto_approval_in_session(
                db,
                order=order,
                recipient_user_ids=recipient_user_ids,
                source_event_type=source_event_type,
                env=env,
            )

    def _finish_auto_approval_in_session(
        self,
        session: Session,
        *,
        order: WorkOrderModel,
        recipient_user_ids: list[str],
        source_event_type: str,
        env: str,
    ) -> list[int]:
        """Finish a locked AUTO row in the caller's business transaction.

        Callers must lock and validate AUTO/PROCESSING before any business write.
        This helper neither commits nor invokes business callbacks.
        """
        now = session.execute(select(func.now())).scalar_one()
        updated = (
            session.query(self._WorkOrder)
            .filter(
                self._WorkOrder.id == order.id,
                self._WorkOrder.env == env,
                self._WorkOrder.approval_mode == WorkOrderApprovalMode.AUTO.value,
                self._WorkOrder.status == WorkOrderStatus.PROCESSING.value,
            )
            .update(
                {
                    self._WorkOrder.status: WorkOrderStatus.APPROVED.value,
                    self._WorkOrder.reviewer_user_id: SYSTEM_REVIEWER_USER_ID,
                    self._WorkOrder.review_remark: None,
                    self._WorkOrder.reviewed_at: now,
                    self._WorkOrder.gmt_modified: now,
                },
                synchronize_session="fetch",
            )
        )
        if updated != 1:
            raise WorkOrderAlreadyProcessedError("AUTO work order is not processing")
        return self._insert_auto_result_notifications(
            session,
            order=order,
            recipient_user_ids=recipient_user_ids,
            source_event_type=source_event_type,
            status=WorkOrderStatus.APPROVED,
            review_remark=None,
            env=env,
        )

    def fail_auto_approval(
        self,
        *,
        work_order_id: int,
        recipient_user_ids: list[str],
        source_event_type: str,
        review_remark: str,
        env: str,
    ) -> list[int]:
        with self._db.transactional_orm_session() as db:
            order = (
                db.query(self._WorkOrder)
                .filter(self._WorkOrder.id == work_order_id, self._WorkOrder.env == env)
                .with_for_update()
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("AUTO work order not found")
            if (
                order.approval_mode != WorkOrderApprovalMode.AUTO.value
                or order.status != WorkOrderStatus.PROCESSING.value
            ):
                raise WorkOrderAlreadyProcessedError("work order is not processing")
            now = db.execute(select(func.now())).scalar_one()
            order.status = WorkOrderStatus.FAILED.value
            order.reviewer_user_id = SYSTEM_REVIEWER_USER_ID
            order.review_remark = review_remark[:512]
            order.reviewed_at = now
            order.gmt_modified = now
            return self._insert_auto_result_notifications(
                db,
                order=order,
                recipient_user_ids=recipient_user_ids,
                source_event_type=source_event_type,
                status=WorkOrderStatus.FAILED,
                review_remark=review_remark,
                env=env,
            )

    def _apply_auto_space_join(
        self, db: Session, *, order: WorkOrderModel, env: str
    ) -> None:
        try:
            space_id = int(order.biz_id)
        except (TypeError, ValueError) as exc:
            raise WorkOrderNotFoundError("invalid Space id in work order") from exc
        space = (
            db.query(self._Space)
            .filter(self._Space.id == space_id, self._Space.env == env)
            .with_for_update()
            .one_or_none()
        )
        if space is None:
            raise WorkOrderNotFoundError("work-order Space not found")
        member = (
            db.query(self._Member)
            .filter(
                self._Member.space_id == space_id,
                self._Member.user_id == order.applicant_user_id,
                self._Member.env == env,
            )
            .one_or_none()
        )
        if member is not None:
            raise WorkOrderApplicantAlreadyMemberError("applicant is already a member")
        db.add(
            self._Member(
                space_id=space_id,
                user_id=order.applicant_user_id,
                user_name=order.applicant_user_id,
                role=SpaceRole.MEMBER.value,
                status="ACTIVE",
                env=env,
                created_by=SYSTEM_REVIEWER_USER_ID,
            )
        )

    def _insert_auto_result_notifications(
        self,
        db: Session,
        *,
        order: WorkOrderModel,
        recipient_user_ids: list[str],
        source_event_type: str,
        status: WorkOrderStatus,
        review_remark: str | None,
        env: str,
    ) -> list[int]:
        """Persist result notices before the caller-owned transaction commits."""
        if not recipient_user_ids:
            raise WorkOrderAccessDeniedError("AUTO result recipients are required")
        event_type = reviewed_event_type_for(
            source_event_type=source_event_type, biz_type=order.biz_type
        )
        title = (
            "自动审批已通过" if status is WorkOrderStatus.APPROVED else "自动审批失败"
        )
        text = (
            "自动审批已通过。"
            if status is WorkOrderStatus.APPROVED
            else f"自动审批失败：{review_remark or '业务处理失败'}"
        )
        content = json.dumps({"text": text, "status": status.value}, ensure_ascii=False)
        notices = []
        for recipient_user_id in dict.fromkeys(recipient_user_ids):
            notice = self._Notification(
                work_order_id=order.id,
                recipient_user_id=recipient_user_id,
                notification_category=NotificationCategory.NOTICE.value,
                event_type=event_type,
                biz_type=order.biz_type,
                biz_id=order.biz_id,
                title=notification_title_for(event_type, title),
                content=content,
                env=env,
            )
            db.add(notice)
            notices.append(notice)
        db.flush()
        return [notice.id for notice in notices]
