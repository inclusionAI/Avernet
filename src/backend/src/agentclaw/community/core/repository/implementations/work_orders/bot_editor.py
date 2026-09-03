"""Transactional persistence for Bot editor request work orders."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_collaborator.models import (
    BotCollaboratorModel,
    CollaboratorRole,
)
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderBotEditorRequestNotAllowedError,
    WorkOrderNotFoundError,
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
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel


class _BotEditorWorkOrderRepository:
    """Own the atomic create/review workflows specific to Bot editors."""

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._Approver = WorkOrderApproverModel
        self._Member = SpaceMemberModel
        self._Bot = BotModel
        self._Collaborator = BotCollaboratorModel

    @staticmethod
    def _new_no() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"WO{stamp}{uuid4().hex[:10].upper()}"

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
    ):
        with self._db.transactional_orm_session() as db:
            # Serializing on the Bot prevents concurrent requests from creating
            # more than one pending application for the same applicant.
            bot = (
                db.query(self._Bot)
                .filter(
                    self._Bot.id == bot_pk,
                    self._Bot.bot_id == bot_id,
                    self._Bot.owner_id == owner_id,
                    self._Bot.space_id == space_id,
                    self._Bot.env == env,
                    self._Bot.is_delete == 0,
                )
                .with_for_update()
                .one_or_none()
            )
            if bot is None:
                raise WorkOrderNotFoundError("Bot not found during request creation")

            member = (
                db.query(self._Member.id)
                .filter(
                    self._Member.space_id == space_id,
                    self._Member.user_id == applicant_user_id,
                    self._Member.status == "ACTIVE",
                    self._Member.env == env,
                )
                .first()
            )
            if member is None:
                raise WorkOrderAccessDeniedError(
                    "applicant must be an active Team Space member"
                )

            collaborator = (
                db.query(self._Collaborator.id)
                .filter(
                    self._Collaborator.bot_pk == bot_pk,
                    self._Collaborator.user_id == applicant_user_id,
                    self._Collaborator.env == env,
                )
                .first()
            )
            if collaborator is not None:
                raise WorkOrderApplicantAlreadyEditorError(
                    "applicant already has Bot editor access"
                )

            pending_candidates = (
                db.query(self._WorkOrder.biz_data)
                .filter(
                    self._WorkOrder.biz_type == WorkOrderBizType.BOT_COLLABORATOR.value,
                    self._WorkOrder.biz_id == bot_id,
                    self._WorkOrder.applicant_user_id == applicant_user_id,
                    self._WorkOrder.status == WorkOrderStatus.PENDING.value,
                    self._WorkOrder.env == env,
                )
                .all()
            )
            if any(
                _bot_pk_from_business_data(raw_data) == bot_pk
                for (raw_data,) in pending_candidates
            ):
                raise WorkOrderAlreadyPendingError("pending request already exists")

            title = WorkOrderMessageTitle.BOT_COLLABORATOR_PENDING.value
            content = WorkOrderMessageContent.BOT_COLLABORATOR_PENDING.value.format(
                applicant_name=applicant_name,
                bot_name=bot_name,
            )
            row = self._WorkOrder(
                work_order_no=self._new_no(),
                biz_type=WorkOrderBizType.BOT_COLLABORATOR.value,
                biz_id=bot_id,
                biz_data=json.dumps(
                    {
                        "bot_pk": bot_pk,
                        "bot_id": bot_id,
                        "bot_name": bot_name,
                        "owner_id": owner_id,
                        "space_id": space_id,
                        "applicant_name": applicant_name,
                        "requested_role": CollaboratorRole.MEMBER.value,
                        "display_title": {
                            WorkOrderStatus.PENDING.value: title,
                            WorkOrderStatus.APPROVED.value: WorkOrderMessageTitle.BOT_COLLABORATOR_APPROVED.value,
                            WorkOrderStatus.REJECTED.value: WorkOrderMessageTitle.BOT_COLLABORATOR_REJECTED.value,
                        },
                        "display_content": {
                            WorkOrderStatus.PENDING.value: content,
                            WorkOrderStatus.APPROVED.value: WorkOrderMessageContent.BOT_COLLABORATOR_APPROVED.value.format(
                                bot_name=bot_name
                            ),
                        },
                    },
                    ensure_ascii=False,
                ),
                applicant_user_id=applicant_user_id,
                apply_reason=apply_reason,
                status=WorkOrderStatus.PENDING.value,
                env=env,
            )
            db.add(row)
            db.flush()
            db.add(
                self._Approver(
                    work_order_id=row.id,
                    approver_user_id=owner_id,
                    status=WorkOrderApproverStatus.PENDING.value,
                    env=env,
                )
            )
            db.add(
                self._Notification(
                    work_order_id=row.id,
                    recipient_user_id=owner_id,
                    notification_category=NotificationCategory.APPROVAL.value,
                    event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
                    biz_type=WorkOrderBizType.BOT_COLLABORATOR.value,
                    biz_id=bot_id,
                    title=notification_title_for(
                        WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value, title
                    ),
                    content=content,
                    env=env,
                )
            )
            db.flush()
            db.refresh(row)
            return row.to_record()

    def review_bot_editor_request(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ):
        with self._db.transactional_orm_session() as db:
            reviewed_at = db.execute(select(func.now())).scalar_one()
            work_order = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.biz_type == WorkOrderBizType.BOT_COLLABORATOR.value,
                    self._WorkOrder.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if work_order is None:
                raise WorkOrderNotFoundError("Bot editor work order not found")
            if work_order.status != WorkOrderStatus.PENDING.value:
                raise WorkOrderAlreadyProcessedError("work order already processed")

            try:
                data = json.loads(work_order.biz_data or "{}")
                bot_pk = int(data["bot_pk"])
                bot_id = str(data["bot_id"])
                owner_id = str(data["owner_id"])
                space_id = int(data["space_id"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise WorkOrderBotEditorRequestNotAllowedError(
                    "work-order Bot identity is invalid"
                ) from exc
            if reviewer_user_id != owner_id:
                raise WorkOrderAccessDeniedError("Bot owner role required")

            approver = (
                db.query(self._Approver)
                .filter(
                    self._Approver.work_order_id == work_order_id,
                    self._Approver.approver_user_id == reviewer_user_id,
                    self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                    self._Approver.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if approver is None:
                raise WorkOrderAccessDeniedError("current user is not an approver")

            bot = (
                db.query(self._Bot)
                .filter(
                    self._Bot.id == bot_pk,
                    self._Bot.bot_id == bot_id,
                    self._Bot.owner_id == owner_id,
                    self._Bot.space_id == space_id,
                    self._Bot.env == env,
                    self._Bot.is_delete == 0,
                )
                .with_for_update()
                .one_or_none()
            )
            if bot is None:
                raise WorkOrderNotFoundError("work-order Bot not found")

            if target_status is WorkOrderStatus.APPROVED:
                active_member = (
                    db.query(self._Member.id)
                    .filter(
                        self._Member.space_id == space_id,
                        self._Member.user_id == work_order.applicant_user_id,
                        self._Member.status == "ACTIVE",
                        self._Member.env == env,
                    )
                    .first()
                )
                if active_member is None:
                    raise WorkOrderBotEditorRequestNotAllowedError(
                        "applicant is no longer an active Team Space member"
                    )
                existing = (
                    db.query(self._Collaborator.id)
                    .filter(
                        self._Collaborator.bot_pk == bot_pk,
                        self._Collaborator.user_id == work_order.applicant_user_id,
                        self._Collaborator.env == env,
                    )
                    .first()
                )
                if existing is None:
                    db.add(
                        self._Collaborator(
                            bot_pk=bot_pk,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            user_id=work_order.applicant_user_id,
                            user_name=work_order.applicant_user_id,
                            role=CollaboratorRole.MEMBER.value,
                            operator_id=reviewer_user_id,
                            env=env,
                        )
                    )

            work_order.status = target_status.value
            work_order.reviewer_user_id = reviewer_user_id
            work_order.review_remark = review_remark
            work_order.reviewed_at = reviewed_at
            work_order.gmt_modified = reviewed_at
            approver.status = target_status.value
            approver.review_remark = review_remark
            approver.reviewed_at = reviewed_at
            approver.gmt_modified = reviewed_at
            db.add(
                self._Notification(
                    work_order_id=work_order_id,
                    recipient_user_id=notification.recipient_user_id,
                    notification_category=notification.notification_category.value,
                    event_type=getattr(
                        notification.event_type, "value", notification.event_type
                    ),
                    biz_type=getattr(
                        notification.biz_type, "value", notification.biz_type
                    ),
                    biz_id=notification.biz_id,
                    title=notification_title_for(
                        getattr(notification.event_type, "value", notification.event_type),
                        notification.title,
                    ),
                    content=notification.content,
                    env=env,
                )
            )
            db.query(self._Notification).filter(
                self._Notification.work_order_id == work_order_id,
                self._Notification.notification_category
                == NotificationCategory.APPROVAL.value,
                self._Notification.env == env,
            ).update(
                {self._Notification.gmt_modified: reviewed_at},
                synchronize_session=False,
            )
            try:
                db.flush()
            except IntegrityError as exc:
                raise WorkOrderApplicantAlreadyEditorError(
                    "unable to create approved Bot editor relation"
                ) from exc
            return WorkOrderReviewResult(
                work_order_id=work_order_id,
                status=target_status,
                decision=WorkOrderDecision(target_status.value),
                reviewer_user_id=reviewer_user_id,
                review_remark=review_remark,
                reviewed_at=reviewed_at,
            )


def _bot_pk_from_business_data(raw: str | None) -> int | None:
    try:
        data = json.loads(raw or "{}")
        return int(data["bot_pk"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
