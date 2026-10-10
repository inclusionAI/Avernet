"""Shared AUTO completion preserves Skill atomicity and recipient semantics."""

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from agentclaw.community.core.models.space_skill import SkillGrant
from agentclaw.community.core.work_orders.errors import WorkOrderAlreadyProcessedError
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from tests.community.core.repository.implementations.test_skill_auto_approval_prerequisite import (
    _claimed_auto_skill_order,
    _work_orders,
    db as db,
)


@pytest.mark.parametrize("fails", [False, True])
def test_skill_terminal_notices_use_explicit_recipients_not_applicant(db, fails):
    _, _, order_id = _claimed_auto_skill_order(db)
    repo = _work_orders(db)
    kwargs = dict(
        work_order_id=order_id,
        recipient_user_ids=["observer", "other", "observer"],
        source_event_type="SKILL_COLLABORATOR_APPLIED",
        env="dev",
    )
    if fails:
        notice_ids = repo.fail_auto_approval(**kwargs, review_remark="business failed")
    else:
        notice_ids = repo.apply_auto_skill_editor_request(**kwargs)
    with db.orm_session() as session:
        notices = (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=order_id)
            .all()
        )
        assert {n.recipient_user_id for n in notices} == {"observer", "other"}
        assert len(notices) == len(notice_ids) == 2
        assert (
            session.query(WorkOrderApproverModel)
            .filter_by(work_order_id=order_id)
            .count()
            == 0
        )
    with pytest.raises(WorkOrderAlreadyProcessedError):
        repo.apply_auto_skill_editor_request(**kwargs)
    with pytest.raises(WorkOrderAlreadyProcessedError):
        repo.fail_auto_approval(**kwargs, review_remark="must not overwrite")
    with db.orm_session() as session:
        assert (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=order_id)
            .count()
            == 2
        )
        assert session.get(WorkOrderModel, order_id).status == (
            "FAILED" if fails else "APPROVED"
        )


def test_failure_notice_write_rolls_back_failed_state(db):
    _, skill_id, order_id = _claimed_auto_skill_order(db)

    def reject_notice(session, _context, _instances):
        if any(isinstance(row, WorkOrderNotificationModel) for row in session.new):
            raise RuntimeError("notice storage unavailable")

    event.listen(Session, "before_flush", reject_notice)
    try:
        with pytest.raises(RuntimeError, match="notice storage unavailable"):
            _work_orders(db).fail_auto_approval(
                work_order_id=order_id,
                recipient_user_ids=["observer"],
                source_event_type="SKILL_COLLABORATOR_APPLIED",
                review_remark="business failed",
                env="dev",
            )
    finally:
        event.remove(Session, "before_flush", reject_notice)
    with db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        assert (order.status, order.reviewer_user_id) == ("PROCESSING", None)
        assert session.query(WorkOrderNotificationModel).count() == 0
        assert (
            session.query(SkillGrant)
            .filter_by(skill_id=skill_id, user_id="applicant-1")
            .count()
            == 0
        )
