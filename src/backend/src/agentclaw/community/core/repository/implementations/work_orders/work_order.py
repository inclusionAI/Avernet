"""SQLAlchemy persistence and transactional state changes for work orders."""

from __future__ import annotations

import json

from injector import inject
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.work_orders.bot_editor import (
    _BotEditorWorkOrderRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.creation import (
    _WorkOrderCreationRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillEditorRequestRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.work_orders.notification import (
    _WorkOrderNotificationRepository,
)
from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyProcessedError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalContext,
    WorkOrderApproverRecord,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderItemType,
    WorkOrderListItem,
    WorkOrderNotificationBadgeSummary,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderReviewResult,
    WorkOrderStatus,
    WorkOrderDecision,
    WorkOrderApproverStatus,
    WorkOrderEventCreatedResult,
    WorkOrderEventType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    reviewed_event_type_for,
    notification_title_for,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderNotificationModel,
    WorkOrderApproverModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


_ADMINISTRATOR_ROLES = ("ADMIN", "ADMINISTRATOR")


class WorkOrderRepository(WorkOrderRepositoryProtocol):
    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        skill_editor_requests: SkillEditorRequestRepositoryProtocol,
    ) -> None:
        self._db = db
        self._WorkOrder = WorkOrderModel
        self._Notification = WorkOrderNotificationModel
        self._Approver = WorkOrderApproverModel
        self._Space = SpaceModel
        self._Member = SpaceMemberModel
        self._bot_editor = _BotEditorWorkOrderRepository(db)
        self._skill_editor = skill_editor_requests
        self._creation = _WorkOrderCreationRepository(db)
        self._notifications = _WorkOrderNotificationRepository(db)

    @staticmethod
    def _new_no() -> str:
        return _WorkOrderCreationRepository._new_no()

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
        return self._creation.create_work_order_event(
            event_category=event_category,
            biz_type=biz_type,
            biz_id=biz_id,
            event_type=event_type,
            applicant_user_id=applicant_user_id,
            approver_user_ids=approver_user_ids,
            recipient_user_ids=recipient_user_ids,
            title=title,
            content=content,
            apply_reason=apply_reason,
            biz_data=biz_data,
            env=env,
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
        return self._creation.create_work_order(
            biz_type=biz_type,
            biz_id=biz_id,
            applicant_user_id=applicant_user_id,
            apply_reason=apply_reason,
            biz_data=biz_data,
            approver_user_ids=approver_user_ids,
            notification_recipient_user_ids=notification_recipient_user_ids,
            env=env,
        )

    def get_approval_context(
        self, *, work_order_id: int, reviewer_user_id: str, env: str
    ) -> WorkOrderApprovalContext:
        with self._db.orm_session() as db:
            order = (
                db.query(self._WorkOrder)
                .filter(self._WorkOrder.id == work_order_id, self._WorkOrder.env == env)
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("work order not found")
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
            source_event_type = source_event[0] if source_event is not None else None
            return WorkOrderApprovalContext(
                work_order=order.to_record(),
                approver=WorkOrderApproverRecord(
                    id=approver.id,
                    work_order_id=approver.work_order_id,
                    approver_user_id=approver.approver_user_id,
                    status=approver.status,
                    review_remark=approver.review_remark,
                    reviewed_at=approver.reviewed_at,
                    env=approver.env,
                    gmt_created=approver.gmt_created,
                    gmt_modified=approver.gmt_modified,
                ),
                source_event_type=source_event_type,
            )

    def process_approval(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        decision: WorkOrderDecision,
        review_remark: str | None,
        env: str,
    ):
        with self._db.transactional_orm_session() as db:
            now = db.execute(select(func.now())).scalar_one()
            order = (
                db.query(self._WorkOrder)
                .filter(self._WorkOrder.id == work_order_id, self._WorkOrder.env == env)
                .with_for_update()
                .one_or_none()
            )
            if order is None:
                raise WorkOrderNotFoundError("work order not found")
            approver = (
                db.query(self._Approver)
                .filter(
                    self._Approver.work_order_id == work_order_id,
                    self._Approver.approver_user_id == reviewer_user_id,
                    self._Approver.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if approver is None:
                raise WorkOrderAccessDeniedError("current user is not an approver")
            if (
                order.status != WorkOrderStatus.PENDING.value
                or approver.status != WorkOrderApproverStatus.PENDING.value
            ):
                raise WorkOrderAlreadyProcessedError("work order already processed")
            target = WorkOrderStatus(decision.value)
            updated = (
                db.query(self._Approver)
                .filter(
                    self._Approver.id == approver.id,
                    self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                )
                .update(
                    {
                        self._Approver.status: decision.value,
                        self._Approver.review_remark: review_remark,
                        self._Approver.reviewed_at: now,
                        self._Approver.gmt_modified: now,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise WorkOrderAlreadyProcessedError("approver already processed")
            db.query(self._WorkOrder).filter(
                self._WorkOrder.id == work_order_id,
                self._WorkOrder.status == WorkOrderStatus.PENDING.value,
            ).update(
                {
                    self._WorkOrder.status: target.value,
                    self._WorkOrder.reviewer_user_id: reviewer_user_id,
                    self._WorkOrder.review_remark: review_remark,
                    self._WorkOrder.reviewed_at: now,
                    self._WorkOrder.gmt_modified: now,
                },
                synchronize_session=False,
            )

            # A unified SPACE_JOIN approval has a domain side effect in
            # addition to the generic work-order state transition: the
            # applicant becomes an active Space member.  Keep this write on
            # the current ORM session so the work order, approver state,
            # membership and result notice commit or roll back together.
            if (
                decision is WorkOrderDecision.APPROVED
                and order.biz_type == WorkOrderBizType.SPACE_JOIN.value
            ):
                try:
                    space_id = int(order.biz_id)
                except (TypeError, ValueError) as exc:
                    raise WorkOrderNotFoundError(
                        "invalid Space id in work order"
                    ) from exc

                space = (
                    db.query(self._Space)
                    .filter(self._Space.id == space_id, self._Space.env == env)
                    .one_or_none()
                )
                if space is None:
                    raise WorkOrderNotFoundError("work-order business object not found")

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
                    # Membership rows are physically deleted when a user
                    # leaves a Space, so any existing row means the applicant
                    # is already an active member and must not be duplicated.
                    raise WorkOrderApplicantAlreadyMemberError(
                        "applicant is already a member"
                    )
                db.add(
                    self._Member(
                        space_id=space_id,
                        user_id=order.applicant_user_id,
                        user_name=order.applicant_user_id,
                        role=SpaceRole.MEMBER.value,
                        status="ACTIVE",
                        env=env,
                        created_by=reviewer_user_id,
                    )
                )

            db.query(self._Approver).filter(
                self._Approver.work_order_id == work_order_id,
                self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                self._Approver.env == env,
            ).update(
                {
                    self._Approver.status: WorkOrderApproverStatus.CANCELLED.value,
                    self._Approver.gmt_modified: now,
                },
                synchronize_session=False,
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
            source_event_type = source_event[0] if source_event is not None else None
            reviewed_event_type = reviewed_event_type_for(
                source_event_type=source_event_type,
                biz_type=order.biz_type,
            )
            result_title = notification_title_for(
                reviewed_event_type, f"{order.biz_type} {target.value}"
            )
            result_content = review_remark
            if (
                order.biz_type == WorkOrderBizType.BOT_FRIEND.value
                and reviewed_event_type
                in {
                    WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED.value,
                    WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED.value,
                }
            ):
                is_human_friend = (
                    reviewed_event_type
                    == WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED.value
                )
                if target is WorkOrderStatus.APPROVED:
                    result_title = (
                        WorkOrderMessageTitle.HUMAN_FRIEND_APPROVED.value
                        if is_human_friend
                        else WorkOrderMessageTitle.BOT_FRIEND_APPROVED.value
                    )
                    result_text = (
                        WorkOrderMessageContent.HUMAN_FRIEND_APPROVED.value
                        if is_human_friend
                        else WorkOrderMessageContent.BOT_FRIEND_APPROVED.value
                    )
                else:
                    result_title = (
                        WorkOrderMessageTitle.HUMAN_FRIEND_REJECTED.value
                        if is_human_friend
                        else WorkOrderMessageTitle.BOT_FRIEND_REJECTED.value
                    )
                    result_text = (
                        WorkOrderMessageContent.HUMAN_FRIEND_REJECTED.value
                        if is_human_friend
                        else WorkOrderMessageContent.BOT_FRIEND_REJECTED.value
                    )
                result_payload = {"text": result_text}
                if review_remark is not None:
                    result_payload["review_remark"] = review_remark
                result_content = json.dumps(
                    result_payload, ensure_ascii=False
                )
            db.add(
                self._Notification(
                    work_order_id=work_order_id,
                    recipient_user_id=order.applicant_user_id,
                    notification_category=NotificationCategory.NOTICE.value,
                    event_type=reviewed_event_type,
                    biz_type=order.biz_type,
                    biz_id=order.biz_id,
                    title=result_title,
                    content=result_content,
                    env=env,
                )
            )
            db.flush()
            return WorkOrderReviewResult(
                work_order_id=work_order_id,
                status=target,
                decision=decision,
                reviewer_user_id=reviewer_user_id,
                review_remark=review_remark,
                reviewed_at=now,
            )

    def create_space_join_request(
        self,
        *,
        space_id: int,
        applicant_user_id: str,
        applicant_name: str,
        apply_reason: str | None,
        env: str,
    ):
        return self._creation.create_space_join_request(
            space_id=space_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            apply_reason=apply_reason,
            env=env,
        )

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
        return self._bot_editor.create_bot_editor_request(
            bot_pk=bot_pk,
            bot_id=bot_id,
            bot_name=bot_name,
            owner_id=owner_id,
            space_id=space_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            apply_reason=apply_reason,
            env=env,
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
        return self._skill_editor.create_skill_editor_request(
            space_id=space_id,
            skill_id=skill_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            apply_reason=apply_reason,
            env=env,
        )

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
    ):
        with self._db.orm_session() as db:
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
                            ),
                        )
                    )
                else:
                    query = query.filter(
                        or_(
                            and_(
                                self._Notification.notification_category
                                == NotificationCategory.APPROVAL.value,
                                self._WorkOrder.status.in_(
                                    [
                                        WorkOrderStatus.APPROVED.value,
                                        WorkOrderStatus.REJECTED.value,
                                    ]
                                ),
                            ),
                            and_(
                                self._Notification.notification_category
                                == NotificationCategory.NOTICE.value,
                                self._Notification.is_read.is_(True),
                            ),
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

            if biz_type is not None:
                query = query.filter(self._WorkOrder.biz_type == biz_type)
            if biz_id is not None:
                query = query.filter(self._WorkOrder.biz_id == biz_id)

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
            approver = (
                db.query(self._Approver.id)
                .filter(
                    self._Approver.work_order_id == work_order_id,
                    self._Approver.approver_user_id == actor_id,
                    self._Approver.env == env,
                )
                .first()
            )
            if (
                work_order.applicant_user_id != actor_id
                and notification is None
                and approver is None
            ):
                return None
            space = None
            space_reference = work_order.biz_id
            if work_order.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value:
                try:
                    space_reference = str(
                        int(json.loads(work_order.biz_data or "{}")["space_id"])
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    space_reference = ""
            try:
                space = (
                    db.query(self._Space)
                    .filter(
                        self._Space.id == int(space_reference), self._Space.env == env
                    )
                    .one_or_none()
                )
            except (TypeError, ValueError):
                pass
            can_approve = bool(
                approver is not None
                and work_order.status == WorkOrderStatus.PENDING.value
                and db.query(self._Approver.id)
                .filter(
                    self._Approver.work_order_id == work_order_id,
                    self._Approver.approver_user_id == actor_id,
                    self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                    self._Approver.env == env,
                )
                .first()
                is not None
            )
            # A work order without a notification has no originating event.
            # Keep these fields empty so the delivery adapter can derive a
            # compatible title from the business type and current status.
            event_type = notification.event_type if notification is not None else None
            title = notification.title if notification is not None else None
            return WorkOrderDetail(
                work_order=work_order.to_record(),
                event_type=event_type,
                title=title,
                content=notification.content if notification is not None else None,
                space_id=space.id if space is not None else 0,
                space_name=space.name if space is not None else "",
                applicant_name=work_order.applicant_user_id,
                can_approve=can_approve,
            )

    def review_skill_editor_request(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        env: str,
    ):
        return self._skill_editor.review_skill_editor_request(
            work_order_id=work_order_id,
            reviewer_user_id=reviewer_user_id,
            review_remark=review_remark,
            target_status=target_status,
            notification=notification,
            env=env,
        )

    def review_space_join(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
        notification: WorkOrderNotificationDraft,
        applicant_user_name: str | None,
        env: str,
    ):
        with self._db.transactional_orm_session() as db:
            reviewed_at = db.execute(select(func.now())).scalar_one()
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
                    self._Member.role.in_(_ADMINISTRATOR_ROLES),
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

            # Keep the legacy Space-join endpoints in sync with the unified
            # approver records. Rows created before the approver table existed
            # are backfilled lazily when they are reviewed.
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
                db.add(
                    self._Approver(
                        work_order_id=work_order_id,
                        approver_user_id=reviewer_user_id,
                        status=target_status.value,
                        review_remark=review_remark,
                        reviewed_at=reviewed_at,
                        env=env,
                    )
                )
            elif approver.status != WorkOrderApproverStatus.PENDING.value:
                raise WorkOrderAlreadyProcessedError("work order already processed")
            else:
                approver.status = target_status.value
                approver.review_remark = review_remark
                approver.reviewed_at = reviewed_at
                approver.gmt_modified = reviewed_at
            db.query(self._Approver).filter(
                self._Approver.work_order_id == work_order_id,
                self._Approver.approver_user_id != reviewer_user_id,
                self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                self._Approver.env == env,
            ).update(
                {
                    self._Approver.status: WorkOrderApproverStatus.CANCELLED.value,
                    self._Approver.gmt_modified: reviewed_at,
                },
                synchronize_session=False,
            )

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
                        user_name=applicant_user_name,
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
                    event_type=getattr(
                        notification.event_type, "value", notification.event_type
                    ),
                    biz_type=getattr(
                        notification.biz_type, "value", notification.biz_type
                    ),
                    biz_id=notification.biz_id,
                    title=notification_title_for(
                        WorkOrderEventType.SPACE_JOIN_REVIEWED.value,
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
                raise WorkOrderApplicantAlreadyMemberError(
                    "unable to create approved membership"
                ) from exc
            return WorkOrderReviewResult(
                work_order_id=work_order_id,
                status=target_status,
                decision=WorkOrderDecision(target_status.value),
                reviewer_user_id=reviewer_user_id,
                review_remark=review_remark,
                reviewed_at=reviewed_at,
            )

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
        return self._bot_editor.review_bot_editor_request(
            work_order_id=work_order_id,
            reviewer_user_id=reviewer_user_id,
            review_remark=review_remark,
            target_status=target_status,
            notification=notification,
            env=env,
        )

    def get_notification(
        self,
        *,
        notification_id: int,
        recipient_user_id: str,
        env: str,
        mark_read: bool,
    ):
        return self._notifications.get_notification(
            notification_id=notification_id,
            recipient_user_id=recipient_user_id,
            env=env,
            mark_read=mark_read,
        )

    def count_unread(self, *, recipient_user_id: str, env: str) -> int:
        return self._notifications.count_unread(
            recipient_user_id=recipient_user_id, env=env
        )

    def get_notification_badge_summary(
        self, *, recipient_user_id: str, env: str
    ) -> WorkOrderNotificationBadgeSummary:
        return self._notifications.get_notification_badge_summary(
            recipient_user_id=recipient_user_id, env=env
        )

    def mark_notification_read(
        self, *, notification_id: int, recipient_user_id: str, env: str
    ):
        return self._notifications.mark_notification_read(
            notification_id=notification_id,
            recipient_user_id=recipient_user_id,
            env=env,
        )

    def mark_all_notifications_read(self, *, recipient_user_id: str, env: str) -> int:
        return self._notifications.mark_all_notifications_read(
            recipient_user_id=recipient_user_id, env=env
        )
