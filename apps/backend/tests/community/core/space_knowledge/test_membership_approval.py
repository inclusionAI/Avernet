"""Both approval repository entrypoints produce membership events atomically."""


from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import KnowledgeSpacePersistenceEffects
from unittest.mock import Mock

import pytest

from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderDecision,
    WorkOrderStatus,
    WorkOrderNotificationDraft,
    NotificationCategory,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderApproverModel,
)
from tests.community.core.space_knowledge.test_membership_sync import setup as setup
from tests.community.core.space_knowledge.test_membership_sync import snapshots


@pytest.mark.parametrize("entrypoint", ["process_approval", "review_space_join"])
@pytest.mark.parametrize("approved", [True, False])
def test_approval_membership_delivery(setup, entrypoint, approved):
    db, spaces, sync, _ = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        order = WorkOrderModel(
            work_order_no="WO-member",
            biz_type="SPACE_JOIN",
            biz_id="7",
            applicant_user_id="alice",
            status="PENDING",
            approval_mode="MANUAL",
            env="pre",
        )
        s.add(order)
        s.flush()
        order_id = order.id
        s.add(
            WorkOrderApproverModel(
                work_order_id=order_id,
                approver_user_id="owner",
                status="PENDING",
                env="pre",
            )
        )
    repo = WorkOrderRepository(db, Mock(), effects=KnowledgeSpacePersistenceEffects())
    if entrypoint == "process_approval":
        repo.process_approval(
            work_order_id=order_id,
            reviewer_user_id="owner",
            decision=WorkOrderDecision.APPROVED
            if approved
            else WorkOrderDecision.REJECTED,
            review_remark=None,
            env="pre",
        )
    else:
        repo.review_space_join(
            work_order_id=order_id,
            reviewer_user_id="owner",
            target_status=WorkOrderStatus.APPROVED
            if approved
            else WorkOrderStatus.REJECTED,
            review_remark=None,
            applicant_user_name="Alice",
            notification=WorkOrderNotificationDraft(
                recipient_user_id="alice",
                notification_category=NotificationCategory.NOTICE,
                event_type="SPACE_JOIN_REVIEWED",
                biz_type="SPACE_JOIN",
                biz_id="7",
                title="Reviewed",
                content={},
            ),
            env="pre",
        )
    assert len(snapshots(db)) == (2 if approved else 1)
    assert (
        spaces.get_member(space_id=7, user_id="alice", env="pre") is not None
    ) == approved


def test_auto_approval_also_records_membership(setup):
    db, _, sync, _ = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        order = WorkOrderModel(
            work_order_no="WO-auto",
            biz_type="SPACE_JOIN",
            biz_id="7",
            applicant_user_id="alice",
            status="PROCESSING",
            approval_mode="AUTO",
            env="pre",
        )
        s.add(order)
        s.flush()
        order_id = order.id
    WorkOrderRepository(db, Mock(), effects=KnowledgeSpacePersistenceEffects()).complete_auto_approval(
        work_order_id=order_id,
        recipient_user_ids=["alice"],
        source_event_type="SPACE_JOIN_REQUESTED",
        env="pre",
    )
    assert len(snapshots(db)) == 2
    assert "alice" in {m["staff_no"] for m in snapshots(db)[1]["members"]}
