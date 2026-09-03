"""Persistence workflows for creating generic work-order records."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyPendingError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderStatus,
    WorkOrderEventType,
    WorkOrderMessageContent,
    WorkOrderTitleKey,
    notification_title_for,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class _WorkOrderCreationRepository:
    """Own generic work-order and space-join creation workflows."""

    _ADMINISTRATOR_ROLES = ("ADMIN", "ADMINISTRATOR")

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._Approver = WorkOrderApproverModel
        self._Space = SpaceModel
        self._Member = SpaceMemberModel

    @staticmethod
    def _new_no() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"WO{stamp}{uuid4().hex[:10].upper()}"

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
        content: str | None,
        apply_reason: str | None,
        biz_data: str | None,
        env: str,
    ) -> WorkOrderEventCreatedResult:
        recipients = (
            approver_user_ids
            if event_category is NotificationCategory.APPROVAL
            else recipient_user_ids
        )
        if not recipients:
            raise WorkOrderNoReviewerError("no work-order recipient")

        with self._db.transactional_orm_session() as db:
            work_order_id: int | None = None
            work_order_no: str | None = None
            if event_category is NotificationCategory.APPROVAL:
                row = self._WorkOrder(
                    work_order_no=self._new_no(),
                    biz_type=biz_type,
                    biz_id=biz_id,
                    biz_data=biz_data,
                    applicant_user_id=applicant_user_id,
                    apply_reason=apply_reason,
                    status=WorkOrderStatus.PENDING.value,
                    env=env,
                )
                db.add(row)
                db.flush()
                work_order_id = row.id
                work_order_no = row.work_order_no
                for user_id in approver_user_ids:
                    db.add(
                        self._Approver(
                            work_order_id=row.id,
                            approver_user_id=user_id,
                            status=WorkOrderApproverStatus.PENDING.value,
                            env=env,
                        )
                    )

            notifications = []
            persisted_title = (
                title
                if event_type == WorkOrderEventType.SPACE_JOIN_APPLIED.value
                else notification_title_for(event_type, title)
            )
            for user_id in recipients:
                notification = self._Notification(
                    work_order_id=work_order_id,
                    recipient_user_id=user_id,
                    notification_category=event_category.value,
                    event_type=event_type,
                    biz_type=biz_type,
                    biz_id=biz_id,
                    title=persisted_title,
                    content=content,
                    env=env,
                )
                db.add(notification)
                notifications.append(notification)
            db.flush()
            return WorkOrderEventCreatedResult(
                event_category=event_category,
                work_order_id=work_order_id,
                work_order_no=work_order_no,
                notification_ids=[notification.id for notification in notifications],
                status=(
                    WorkOrderEventStatus.PENDING
                    if event_category is NotificationCategory.APPROVAL
                    else WorkOrderEventStatus.CREATED
                ),
            )

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
    ):
        approver_user_ids = list(dict.fromkeys(approver_user_ids))
        notification_recipient_user_ids = list(
            dict.fromkeys(notification_recipient_user_ids)
        )
        with self._db.transactional_orm_session() as db:
            row = self._WorkOrder(
                work_order_no=self._new_no(),
                biz_type=biz_type,
                biz_id=biz_id,
                biz_data=biz_data,
                applicant_user_id=applicant_user_id,
                apply_reason=apply_reason,
                status=WorkOrderStatus.PENDING.value,
                env=env,
            )
            db.add(row)
            db.flush()
            category = (
                NotificationCategory.APPROVAL
                if approver_user_ids
                else NotificationCategory.NOTICE
            )
            recipients = approver_user_ids or notification_recipient_user_ids
            if not recipients:
                raise WorkOrderNoReviewerError("no work-order recipient")
            for user_id in recipients:
                if approver_user_ids:
                    db.add(
                        self._Approver(
                            work_order_id=row.id,
                            approver_user_id=user_id,
                            status=WorkOrderApproverStatus.PENDING.value,
                            env=env,
                        )
                    )
                db.add(
                    self._Notification(
                        work_order_id=row.id,
                        recipient_user_id=user_id,
                        notification_category=category.value,
                        event_type=biz_type,
                        biz_type=biz_type,
                        biz_id=biz_id,
                        title=biz_type,
                        content=apply_reason,
                        env=env,
                    )
                )
            db.flush()
            db.refresh(row)
            return row.to_record()

    def create_space_join_request(
        self,
        *,
        space_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str | None,
        env: str,
    ):
        with self._db.transactional_orm_session() as db:
            # The DDL cannot express a partial unique key for status=PENDING.
            # Locking the Space serializes competing applications for it.
            space = (
                db.query(self._Space)
                .filter(self._Space.id == space_id, self._Space.env == env)
                .with_for_update()
                .one_or_none()
            )
            if space is None:
                raise WorkOrderNotFoundError("space not found during request creation")

            pending = (
                db.query(self._WorkOrder.id)
                .filter(
                    self._WorkOrder.biz_id == str(space_id),
                    self._WorkOrder.applicant_user_id == applicant_user_id,
                    self._WorkOrder.status == WorkOrderStatus.PENDING.value,
                    self._WorkOrder.env == env,
                )
                .first()
            )
            if pending is not None:
                raise WorkOrderAlreadyPendingError("pending request already exists")

            owners = (
                db.query(self._Member.user_id)
                .filter(
                    self._Member.space_id == space_id,
                    self._Member.role.in_(self._ADMINISTRATOR_ROLES),
                    self._Member.env == env,
                    self._Member.status == "ACTIVE",
                )
                .all()
            )
            if not owners:
                raise WorkOrderNoReviewerError("space has no owner")

            row = self._WorkOrder(
                work_order_no=self._new_no(),
                biz_type=WorkOrderBizType.SPACE_JOIN.value,
                biz_id=str(space_id),
                biz_data=None,
                applicant_user_id=applicant_user_id,
                apply_reason=apply_reason,
                status=WorkOrderStatus.PENDING.value,
                env=env,
            )
            db.add(row)
            db.flush()
            title = WorkOrderTitleKey.SPACE_JOIN_PENDING.value
            content = WorkOrderMessageContent.SPACE_JOIN_PENDING.value.format(
                applicant_name=applicant_name, space_name=space.name
            )
            for (owner_id,) in owners:
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
                        event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
                        biz_type=WorkOrderBizType.SPACE_JOIN.value,
                        biz_id=str(space_id),
                        title=title,
                        content=content,
                        env=env,
                    )
                )
            db.flush()
            db.refresh(row)
            return row.to_record()
