"""Transactional repository operations for automatic work-order approval."""

from __future__ import annotations

from sqlalchemy import func, select

from agentclaw.community.core.work_orders.errors import (
    WorkOrderAlreadyProcessedError,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderApproverStatus,
    WorkOrderStatus,
)


class _AutoApprovalWorkOrderRepository:
    """Provide automatic approval state transitions for work orders."""

    def claim_auto_approval(
        self, *, work_order_id: int, reviewer_user_id: str, env: str
    ) -> None:
        with self._db.transactional_orm_session() as db:
            updated = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.env == env,
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

    def finalize_auto_approval(
        self, *, work_order_id: int, reviewer_user_id: str, env: str
    ) -> None:
        with self._db.transactional_orm_session() as db:
            now = db.execute(select(func.now())).scalar_one()
            db.query(self._Approver).filter(
                self._Approver.work_order_id == work_order_id,
                self._Approver.env == env,
                self._Approver.approver_user_id == reviewer_user_id,
            ).update(
                {
                    self._Approver.status: WorkOrderApproverStatus.APPROVED.value,
                    self._Approver.reviewed_at: now,
                    self._Approver.gmt_modified: now,
                },
                synchronize_session=False,
            )
            db.query(self._WorkOrder).filter(
                self._WorkOrder.id == work_order_id,
                self._WorkOrder.env == env,
            ).update(
                {
                    self._WorkOrder.status: WorkOrderStatus.APPROVED.value,
                    self._WorkOrder.reviewer_user_id: reviewer_user_id,
                    self._WorkOrder.reviewed_at: now,
                    self._WorkOrder.gmt_modified: now,
                },
                synchronize_session=False,
            )

    def mark_auto_approval_failed(
        self,
        *,
        work_order_id: int,
        reviewer_user_id: str,
        review_remark: str,
        env: str,
    ) -> None:
        with self._db.transactional_orm_session() as db:
            now = db.execute(select(func.now())).scalar_one()
            updated = (
                db.query(self._WorkOrder)
                .filter(
                    self._WorkOrder.id == work_order_id,
                    self._WorkOrder.env == env,
                    self._WorkOrder.status == WorkOrderStatus.PROCESSING.value,
                )
                .update(
                    {
                        self._WorkOrder.status: WorkOrderStatus.FAILED.value,
                        self._WorkOrder.reviewer_user_id: reviewer_user_id,
                        self._WorkOrder.review_remark: review_remark[:512],
                        self._WorkOrder.reviewed_at: now,
                        self._WorkOrder.gmt_modified: now,
                    },
                    synchronize_session=False,
                )
            )
            if updated == 1:
                db.query(self._Approver).filter(
                    self._Approver.work_order_id == work_order_id,
                    self._Approver.status == WorkOrderApproverStatus.PENDING.value,
                    self._Approver.env == env,
                ).update(
                    {self._Approver.status: WorkOrderApproverStatus.CANCELLED.value},
                    synchronize_session=False,
                )
