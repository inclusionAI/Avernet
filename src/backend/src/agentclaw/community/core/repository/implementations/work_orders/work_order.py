"""SQLAlchemy persistence and transactional state changes for work orders."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from injector import inject
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyPendingError,
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyProcessedError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderNotificationDetail,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderReviewResult,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


_ADMINISTRATOR_ROLE = "ADMINISTRATOR"


class WorkOrderRepository(WorkOrderRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._Space = SpaceModel
        self._Member = SpaceMemberModel

    @staticmethod
    def _new_no() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"WO{stamp}{uuid4().hex[:10].upper()}"

    def create_space_join_request(
        self,
        *,
        space_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str,
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
                    self._WorkOrder.biz_type == WorkOrderBizType.SPACE_JOIN.value,
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
                    self._Member.role == _ADMINISTRATOR_ROLE,
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
                applicant_user_id=applicant_user_id,
                apply_reason=apply_reason,
                status=WorkOrderStatus.PENDING.value,
                env=env,
            )
            db.add(row)
            db.flush()
            title = WorkOrderMessageTitle.SPACE_JOIN_PENDING.value
            content = WorkOrderMessageContent.SPACE_JOIN_PENDING.value.format(
                applicant_name=applicant_name, space_name=space.name
            )
            for (owner_id,) in owners:
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

    def list_items(
        self,
        *,
        actor_id: str,
        env: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
        offset: int,
        limit: int,
    ):
        with self._db.orm_session() as db:
            owner_space_ids = {
                str(space_id)
                for (space_id,) in db.query(self._Member.space_id)
                .filter(
                    self._Member.user_id == actor_id,
                    self._Member.role == _ADMINISTRATOR_ROLE,
                    self._Member.env == env,
                    self._Member.status == "ACTIVE",
                )
                .all()
            }
            query = db.query(self._WorkOrder, self._Notification)
            if query_type is WorkOrderQueryType.INITIATED_BY_ME:
                query = query.outerjoin(
                    self._Notification,
                    and_(
                        self._Notification.work_order_id == self._WorkOrder.id,
                        self._Notification.recipient_user_id == actor_id,
                        self._Notification.env == env,
                    ),
                ).filter(
                    self._WorkOrder.applicant_user_id == actor_id,
                    self._WorkOrder.env == env,
                )
            else:
                query = query.join(
                    self._Notification,
                    self._Notification.work_order_id == self._WorkOrder.id,
                ).filter(
                    self._Notification.recipient_user_id == actor_id,
                    self._Notification.env == env,
                    self._WorkOrder.env == env,
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
                            ),
                        )
                    )
                else:
                    query = query.filter(
                        self._Notification.notification_category
                        == NotificationCategory.APPROVAL.value,
                        self._WorkOrder.status.in_(
                            [
                                WorkOrderStatus.APPROVED.value,
                                WorkOrderStatus.REJECTED.value,
                            ]
                        ),
                    )

            if item_type is not WorkOrderItemType.ALL:
                if query_type is WorkOrderQueryType.INITIATED_BY_ME:
                    if item_type is WorkOrderItemType.NOTICE:
                        query = query.filter(
                            self._Notification.notification_category
                            == NotificationCategory.NOTICE.value
                        )
                    else:
                        query = query.filter(
                            or_(
                                self._Notification.id.is_(None),
                                self._Notification.notification_category
                                == NotificationCategory.APPROVAL.value,
                            )
                        )
                else:
                    query = query.filter(
                        self._Notification.notification_category == item_type.value
                    )

            total = query.count()
            rows = (
                query.order_by(
                    func.coalesce(
                        self._Notification.gmt_modified,
                        self._WorkOrder.gmt_modified,
                    ).desc(),
                    self._WorkOrder.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [
                WorkOrderListItem(
                    work_order=work_order.to_record(),
                    notification=(
                        notification.to_record() if notification is not None else None
                    ),
                    can_approve=(
                        query_type is not WorkOrderQueryType.INITIATED_BY_ME
                        and work_order.status == WorkOrderStatus.PENDING.value
                        and notification is not None
                        and notification.notification_category
                        == NotificationCategory.APPROVAL.value
                        and work_order.biz_id in owner_space_ids
                    ),
                )
                for work_order, notification in rows
            ]

    def get_detail(self, *, work_order_id: int, actor_id: str, env: str):
        with self._db.orm_session() as db:
            work_order = (
                db.query(self._WorkOrder)
                .filter(self._WorkOrder.id == work_order_id, self._WorkOrder.env == env)
                .one_or_none()
            )
            if work_order is None:
                return None
            notification = (
                db.query(self._Notification)
                .filter(
                    self._Notification.work_order_id == work_order_id,
                    self._Notification.recipient_user_id == actor_id,
                    self._Notification.env == env,
                )
                .order_by(self._Notification.gmt_modified.desc())
                .first()
            )
            if work_order.applicant_user_id != actor_id and notification is None:
                return None
            space = (
                db.query(self._Space)
                .filter(
                    self._Space.id == int(work_order.biz_id), self._Space.env == env
                )
                .one_or_none()
            )
            if space is None:
                return None
            is_current_owner = (
                db.query(self._Member.id)
                .filter(
                    self._Member.space_id == space.id,
                    self._Member.user_id == actor_id,
                    self._Member.role == _ADMINISTRATOR_ROLE,
                    self._Member.env == env,
                    self._Member.status == "ACTIVE",
                )
                .first()
                is not None
            )
            can_approve = bool(
                notification is not None
                and notification.notification_category
                == NotificationCategory.APPROVAL.value
                and work_order.status == WorkOrderStatus.PENDING.value
                and is_current_owner
            )
            event_type = (
                WorkOrderEventType(notification.event_type)
                if notification is not None
                else WorkOrderEventType.SPACE_JOIN_APPLIED
            )
            title = (
                notification.title
                if notification is not None
                else WorkOrderMessageTitle.SPACE_JOIN_PENDING.value
            )
            return WorkOrderDetail(
                work_order=work_order.to_record(),
                event_type=event_type,
                title=title,
                space_id=space.id,
                space_name=space.name,
                applicant_name=work_order.applicant_user_id,
                can_approve=can_approve,
            )

    def review_space_join(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ):
        reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._db.transactional_orm_session() as db:
            work_order = (
                db.query(self._WorkOrder)
                .filter(self._WorkOrder.id == work_order_id, self._WorkOrder.env == env)
                .one_or_none()
            )
            if work_order is None:
                raise WorkOrderNotFoundError("work order not found")
            owner = (
                db.query(self._Member.id)
                .filter(
                    self._Member.space_id == int(work_order.biz_id),
                    self._Member.user_id == reviewer_user_id,
                    self._Member.role == _ADMINISTRATOR_ROLE,
                    self._Member.env == env,
                    self._Member.status == "ACTIVE",
                )
                .first()
            )
            if owner is None:
                raise WorkOrderAccessDeniedError("space owner role required")
            updated = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.status == WorkOrderStatus.PENDING.value,
                    self._WorkOrder.env == env,
                )
                .update(
                    {
                        self._WorkOrder.status: target_status.value,
                        self._WorkOrder.reviewer_user_id: reviewer_user_id,
                        self._WorkOrder.review_remark: review_remark,
                        self._WorkOrder.reviewed_at: reviewed_at,
                        self._WorkOrder.gmt_modified: reviewed_at,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise WorkOrderAlreadyProcessedError("work order already processed")

            space_id = int(work_order.biz_id)
            space = (
                db.query(self._Space)
                .filter(self._Space.id == space_id, self._Space.env == env)
                .one_or_none()
            )
            if space is None:
                raise WorkOrderNotFoundError("work-order business object not found")

            if target_status is WorkOrderStatus.APPROVED:
                existing = (
                    db.query(self._Member.id)
                    .filter(
                        self._Member.space_id == space_id,
                        self._Member.user_id == work_order.applicant_user_id,
                        self._Member.env == env,
                    )
                    .first()
                )
                if existing is not None:
                    raise WorkOrderApplicantAlreadyMemberError(
                        "applicant is already a member"
                    )
                db.add(
                    self._Member(
                        space_id=space_id,
                        user_id=work_order.applicant_user_id,
                        role=SpaceRole.MEMBER.value,
                        env=env,
                        created_by=reviewer_user_id,
                    )
                )

            db.add(
                self._Notification(
                    work_order_id=work_order_id,
                    recipient_user_id=notification.recipient_user_id,
                    notification_category=notification.notification_category.value,
                    event_type=notification.event_type.value,
                    biz_type=notification.biz_type.value,
                    biz_id=notification.biz_id,
                    title=notification.title,
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
                raise WorkOrderApplicantAlreadyMemberError(
                    "unable to create approved membership"
                ) from exc
            return WorkOrderReviewResult(
                work_order_id=work_order_id,
                status=target_status,
                reviewer_user_id=reviewer_user_id,
                review_remark=review_remark,
                reviewed_at=reviewed_at,
            )

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
                now = datetime.now(timezone.utc).replace(tzinfo=None)
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
                    can_approve = bool(
                        row.notification_category == NotificationCategory.APPROVAL.value
                        and status is WorkOrderStatus.PENDING
                        and db.query(self._Member.id)
                        .filter(
                            self._Member.space_id == int(work_order.biz_id),
                            self._Member.user_id == recipient_user_id,
                            self._Member.role == _ADMINISTRATOR_ROLE,
                            self._Member.env == env,
                            self._Member.status == "ACTIVE",
                        )
                        .first()
                        is not None
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
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._db.orm_session() as db:
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
