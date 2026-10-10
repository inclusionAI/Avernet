"""Recipient inbox and applicant work-order listing projections."""

from sqlalchemy import and_, func, or_

from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderApproverStatus,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderQueryType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class _WorkOrderListingRepository:
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._Approver = WorkOrderApproverModel

    def list_items(
        self,
        *,
        actor_id: str,
        env: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
        biz_type: str | None = None,
        biz_id: str | None = None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[WorkOrderListItem]]:
        initiated_orders = (
            query_type is WorkOrderQueryType.INITIATED_BY_ME
            and item_type is not WorkOrderItemType.NOTICE
        )
        finished_statuses = (
            WorkOrderStatus.APPROVED.value,
            WorkOrderStatus.REJECTED.value,
            WorkOrderStatus.FAILED.value,
        )
        with self._db.orm_session() as db:
            query = db.query(self._WorkOrder, self._Notification)
            if query_type is WorkOrderQueryType.INITIATED_BY_ME:
                if initiated_orders:
                    query = db.query(self._WorkOrder)
                else:
                    query = query.outerjoin(
                        self._Notification,
                        and_(
                            self._Notification.work_order_id == self._WorkOrder.id,
                            self._Notification.recipient_user_id == actor_id,
                            self._Notification.env == env,
                        ),
                    )
                query = query.filter(
                    self._WorkOrder.env == env,
                    or_(
                        self._WorkOrder.applicant_user_id == actor_id,
                        and_(
                            self._WorkOrder.approval_mode
                            == WorkOrderApprovalMode.AUTO.value,
                            self._WorkOrder.reviewer_user_id == actor_id,
                        ),
                    ),
                )
            else:
                query = (
                    db.query(self._WorkOrder, self._Notification)
                    .select_from(self._Notification)
                    .outerjoin(
                        self._WorkOrder,
                        self._Notification.work_order_id == self._WorkOrder.id,
                    )
                    .filter(
                        self._Notification.recipient_user_id == actor_id,
                        self._Notification.env == env,
                        or_(self._WorkOrder.env == env, self._WorkOrder.id.is_(None)),
                    )
                )
                if query_type is WorkOrderQueryType.PENDING_FOR_ME:
                    query = query.filter(
                        or_(
                            and_(
                                self._Notification.notification_category
                                == NotificationCategory.APPROVAL.value,
                                self._WorkOrder.status == WorkOrderStatus.PENDING.value,
                            ),
                            and_(
                                self._Notification.notification_category
                                == NotificationCategory.NOTICE.value,
                                self._Notification.is_read.is_(False),
                                or_(
                                    self._WorkOrder.id.is_(None),
                                    self._WorkOrder.status.notin_(finished_statuses),
                                ),
                            ),
                        )
                    )
                else:
                    query = query.filter(
                        or_(
                            and_(
                                self._Notification.notification_category
                                == NotificationCategory.APPROVAL.value,
                                self._WorkOrder.status.in_(finished_statuses),
                            ),
                            and_(
                                self._Notification.notification_category
                                == NotificationCategory.NOTICE.value,
                                or_(
                                    self._Notification.is_read.is_(True),
                                    self._WorkOrder.status.in_(finished_statuses),
                                ),
                            ),
                        ),
                    )

            if not initiated_orders and item_type is not WorkOrderItemType.ALL:
                query = query.filter(
                    self._Notification.notification_category == item_type.value
                )

            if biz_type is not None:
                query = query.filter(self._WorkOrder.biz_type == biz_type)
            if biz_id is not None:
                query = query.filter(self._WorkOrder.biz_id == biz_id)

            total = query.count()
            modified = (
                self._WorkOrder.gmt_modified
                if initiated_orders
                else func.coalesce(
                    self._Notification.gmt_modified, self._WorkOrder.gmt_modified
                )
            )
            rows = (
                query.order_by(
                    modified.desc(),
                    self._WorkOrder.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            if initiated_orders:
                rows = [(order, None) for order in rows]
            items = []
            for work_order, notification in rows:
                is_approver = (
                    work_order is not None
                    and db.query(self._Approver.id)
                    .filter(
                        self._Approver.work_order_id == work_order.id,
                        self._Approver.approver_user_id == actor_id,
                        self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                        self._Approver.env == env,
                    )
                    .first()
                    is not None
                )
                items.append(
                    WorkOrderListItem(
                        work_order=work_order.to_record()
                        if work_order is not None
                        else None,
                        notification=notification.to_record()
                        if notification is not None
                        else None,
                        can_approve=(
                            query_type is not WorkOrderQueryType.INITIATED_BY_ME
                            and work_order is not None
                            and work_order.status == WorkOrderStatus.PENDING.value
                            and notification is not None
                            and notification.notification_category
                            == NotificationCategory.APPROVAL.value
                            and is_approver
                        ),
                    )
                )
            return total, items
