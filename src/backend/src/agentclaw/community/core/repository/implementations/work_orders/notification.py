"""Recipient notification persistence for work orders."""

from __future__ import annotations

from sqlalchemy import and_, func, select

from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderNotificationBadgeSummary,
    WorkOrderNotificationDetail,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


_ADMINISTRATOR_ROLES = ("ADMIN", "ADMINISTRATOR")


class _WorkOrderNotificationRepository:
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._Approver = WorkOrderApproverModel
        self._Member = SpaceMemberModel

    def get_notification(
        self,
        *,
        notification_id: int,
        recipient_user_id: str,
        env: str,
        mark_read: bool,
    ):
        with self._db.orm_session() as db:
            row = (
                db.query(self._Notification)
                .filter(
                    self._Notification.id == notification_id,
                    self._Notification.recipient_user_id == recipient_user_id,
                    self._Notification.env == env,
                )
                .one_or_none()
            )
            if row is None:
                return None
            if mark_read and not row.is_read:
                now = db.execute(select(func.now())).scalar_one()
                row.is_read = True
                row.read_at = now
                row.gmt_modified = now
                db.flush()
                db.refresh(row)
            status = None
            can_approve = False
            if row.work_order_id is not None:
                work_order = (
                    db.query(self._WorkOrder)
                    .filter(
                        self._WorkOrder.id == row.work_order_id,
                        self._WorkOrder.env == env,
                    )
                    .one_or_none()
                )
                if work_order is not None:
                    status = WorkOrderStatus(work_order.status)
                    is_approver = (
                        db.query(self._Approver.id)
                        .filter(
                            self._Approver.work_order_id == work_order.id,
                            self._Approver.approver_user_id == recipient_user_id,
                            self._Approver.status
                            == WorkOrderApproverStatus.PENDING.value,
                            self._Approver.env == env,
                        )
                        .first()
                        is not None
                    )
                    # Legacy Space-join rows created before the approver table
                    # existed have no approver record. Keep their notification
                    # detail usable until they are reviewed and backfilled.
                    if (
                        not is_approver
                        and work_order.biz_type == WorkOrderBizType.SPACE_JOIN.value
                    ):
                        try:
                            is_approver = (
                                db.query(self._Member.id)
                                .filter(
                                    self._Member.space_id == int(work_order.biz_id),
                                    self._Member.user_id == recipient_user_id,
                                    self._Member.role.in_(_ADMINISTRATOR_ROLES),
                                    self._Member.env == env,
                                    self._Member.status == "ACTIVE",
                                )
                                .first()
                                is not None
                            )
                        except (TypeError, ValueError):
                            is_approver = False
                    can_approve = bool(
                        row.notification_category == NotificationCategory.APPROVAL.value
                        and status is WorkOrderStatus.PENDING
                        and is_approver
                    )
            return WorkOrderNotificationDetail(
                notification=row.to_record(),
                work_order_status=status,
                can_approve=can_approve,
            )

    def count_unread(self, *, recipient_user_id: str, env: str) -> int:
        with self._db.orm_session() as db:
            return (
                db.query(func.count(self._Notification.id))
                .filter(
                    self._Notification.recipient_user_id == recipient_user_id,
                    self._Notification.is_read.is_(False),
                    self._Notification.env == env,
                )
                .scalar()
                or 0
            )

    def get_notification_badge_summary(
        self, *, recipient_user_id: str, env: str
    ) -> WorkOrderNotificationBadgeSummary:
        with self._db.orm_session() as db:
            unread_count = (
                db.query(func.count(self._Notification.id))
                .filter(
                    self._Notification.recipient_user_id == recipient_user_id,
                    self._Notification.is_read.is_(False),
                    self._Notification.env == env,
                )
                .scalar()
                or 0
            )
            unread_notice_count = (
                db.query(func.count(self._Notification.id))
                .filter(
                    self._Notification.recipient_user_id == recipient_user_id,
                    self._Notification.notification_category
                    == NotificationCategory.NOTICE.value,
                    self._Notification.is_read.is_(False),
                    self._Notification.env == env,
                )
                .scalar()
                or 0
            )
            pending_approval_count = (
                db.query(func.count(func.distinct(self._WorkOrder.id)))
                .join(
                    self._Notification,
                    self._Notification.work_order_id == self._WorkOrder.id,
                )
                .join(
                    self._Approver,
                    and_(
                        self._Approver.work_order_id == self._WorkOrder.id,
                        self._Approver.approver_user_id == recipient_user_id,
                        self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                        self._Approver.env == env,
                    ),
                )
                .filter(
                    self._Notification.recipient_user_id == recipient_user_id,
                    self._Notification.notification_category
                    == NotificationCategory.APPROVAL.value,
                    self._Notification.env == env,
                    self._WorkOrder.status == WorkOrderStatus.PENDING.value,
                    self._WorkOrder.env == env,
                )
                .scalar()
                or 0
            )
            return WorkOrderNotificationBadgeSummary(
                unread_count=unread_count,
                pending_approval_count=pending_approval_count,
                unread_notice_count=unread_notice_count,
                badge_count=pending_approval_count + unread_notice_count,
            )

    def mark_notification_read(
        self, *, notification_id: int, recipient_user_id: str, env: str
    ):
        result = self.get_notification(
            notification_id=notification_id,
            recipient_user_id=recipient_user_id,
            env=env,
            mark_read=True,
        )
        return result.notification if result is not None else None

    def mark_all_notifications_read(self, *, recipient_user_id: str, env: str) -> int:
        with self._db.orm_session() as db:
            now = db.execute(select(func.now())).scalar_one()
            return (
                db.query(self._Notification)
                .filter(
                    self._Notification.recipient_user_id == recipient_user_id,
                    self._Notification.is_read.is_(False),
                    self._Notification.env == env,
                )
                .update(
                    {
                        self._Notification.is_read: True,
                        self._Notification.read_at: now,
                        self._Notification.gmt_modified: now,
                    },
                    synchronize_session=False,
                )
            )
