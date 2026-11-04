"""Recipient notification persistence for work orders."""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.work_orders.errors import (
    WorkOrderInvalidEventError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    notification_title_for,
    WorkOrderApproverStatus,
    WorkOrderBizType,
    WorkOrderNotificationBadgeSummary,
    WorkOrderNotificationDetail,
    WorkOrderStatus,
    WorkOrderApprovalMode,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationGroupModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


_ADMINISTRATOR_ROLES = ("ADMIN", "ADMINISTRATOR")


class _WorkOrderNotificationRepository:
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._NotificationGroup = WorkOrderNotificationGroupModel
        self._Approver = WorkOrderApproverModel
        self._Member = SpaceMemberModel

    def merge_notice(
        self,
        *,
        recipient_user_id: str,
        event_type: str,
        group_id: str,
        biz_type: str,
        biz_id: str,
        title: str,
        content_factory: Callable[
            [dict[str, object] | None], dict[str, object]
        ],
        env: str,
    ) -> int:
        """Atomically create one notice or merge it into the open group.

        The callback receives the persisted content object so business callers
        own aggregation semantics while this repository owns locking,
        uniqueness, persistence, and read-state transitions. Concurrent writers
        sharing the same group key serialize through the unique group row.
        """

        for attempt in range(3):
            try:
                return self._merge_notice_once(
                    recipient_user_id=recipient_user_id,
                    event_type=event_type,
                    group_id=group_id,
                    biz_type=biz_type,
                    biz_id=biz_id,
                    title=title,
                    content_factory=content_factory,
                    env=env,
                )
            except IntegrityError:
                if attempt == 2:
                    raise

    def _merge_notice_once(
        self,
        *,
        recipient_user_id: str,
        event_type: str,
        group_id: str,
        biz_type: str,
        biz_id: str,
        title: str,
        content_factory: Callable[
            [dict[str, object] | None], dict[str, object]
        ],
        env: str,
    ) -> int:
        with self._db.transactional_orm_session() as db:
            group = (
                db.query(self._NotificationGroup)
                .filter(
                    self._NotificationGroup.recipient_user_id == recipient_user_id,
                    self._NotificationGroup.event_type == event_type,
                    self._NotificationGroup.group_id == group_id,
                    self._NotificationGroup.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            current_content: dict[str, object] | None = None
            if group is not None:
                notification = (
                    db.query(self._Notification)
                    .filter(
                        self._Notification.id == group.notification_id,
                        self._Notification.recipient_user_id == recipient_user_id,
                        self._Notification.env == env,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if notification is None or notification.is_read:
                    # A read notice must remain immutable. Close its group so a
                    # later event opens a fresh aggregate and read timestamp.
                    db.delete(group)
                    db.flush()
                    group = None
                else:
                    current_content = self._decode_notice_content(
                        notification.content
                    )

            if group is None:
                # Inserting the unique group first serializes two first-time
                # writers before either creates a visible notice row. A loser
                # rolls back and retries against the successful writer's group.
                placeholder = self._NotificationGroup(
                    recipient_user_id=recipient_user_id,
                    event_type=event_type,
                    group_id=group_id,
                    notification_id=0,
                    env=env,
                )
                db.add(placeholder)
                db.flush()
                notification = self._Notification(
                    work_order_id=None,
                    recipient_user_id=recipient_user_id,
                    notification_category=NotificationCategory.NOTICE.value,
                    event_type=event_type,
                    biz_type=biz_type,
                    biz_id=biz_id,
                    title=notification_title_for(event_type, title),
                    content=json.dumps(content_factory(None), ensure_ascii=False),
                    env=env,
                )
                db.add(notification)
                db.flush()
                placeholder.notification_id = notification.id
                db.flush()
                return notification.id

            next_content = content_factory(current_content)
            if not isinstance(next_content, dict):
                raise WorkOrderInvalidEventError(
                    "aggregate notice content must be an object"
                )
            notification.event_type = event_type
            notification.biz_type = biz_type
            notification.biz_id = biz_id
            notification.title = notification_title_for(event_type, title)
            notification.content = json.dumps(next_content, ensure_ascii=False)
            db.flush()
            return notification.id or group.notification_id

    @staticmethod
    def _decode_notice_content(raw: str | None) -> dict[str, object] | None:
        if raw is None or not raw.strip():
            return None
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise WorkOrderInvalidEventError(
                "aggregate notice content must be a JSON object"
            )
        return decoded

    def get_notification(
        self,
        *,
        notification_id: int,
        recipient_user_id: str,
        env: str,
        mark_read: bool,
    ):
        with self._db.transactional_orm_session() as db:
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
                groups = (
                    db.query(self._NotificationGroup)
                    .filter(
                        self._NotificationGroup.notification_id == notification_id,
                        self._NotificationGroup.recipient_user_id == recipient_user_id,
                        self._NotificationGroup.env == env,
                    )
                    .with_for_update()
                    .all()
                )
                for group in groups:
                    db.delete(group)
                db.flush()
                db.refresh(row)
            status = None
            approval_mode = None
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
                    approval_mode = WorkOrderApprovalMode(
                        work_order.approval_mode or WorkOrderApprovalMode.MANUAL.value
                    )
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
                approval_mode=approval_mode,
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
        with self._db.transactional_orm_session() as db:
            now = db.execute(select(func.now())).scalar_one()
            groups = (
                db.query(self._NotificationGroup)
                .filter(
                    self._NotificationGroup.recipient_user_id == recipient_user_id,
                    self._NotificationGroup.env == env,
                )
                .with_for_update()
                .all()
            )
            for group in groups:
                db.delete(group)
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
