"""Tests for the Skill-owned Work Order approval handler seam."""

import json
from datetime import datetime
from unittest.mock import MagicMock

from agentclaw.community.core.skill_center.services.skill_collaborator_approval_handler import (
    SkillCollaboratorApprovalHandler,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderRecord,
    WorkOrderReviewResult,
    WorkOrderStatus,
)


def test_skill_collaborator_handler_delegates_atomic_approval() -> None:
    now = datetime(2026, 8, 26, 8, 0, 0)
    repository = MagicMock()
    expected = WorkOrderReviewResult(
        work_order_id=11,
        status=WorkOrderStatus.APPROVED,
        reviewer_user_id="owner-1",
        review_remark=None,
        reviewed_at=now,
    )
    repository.review_skill_editor_request.return_value = expected
    handler = SkillCollaboratorApprovalHandler(repository, lambda: "test")
    detail = WorkOrderDetail(
        work_order=WorkOrderRecord(
            id=11,
            work_order_no="WO-11",
            biz_type=WorkOrderBizType.SKILL_COLLABORATOR,
            biz_id="9",
            biz_data=json.dumps(
                {"space_id": 7, "skill_id": 9, "skill_name": "Review Skill"}
            ),
            applicant_user_id="member-1",
            apply_reason="maintain together",
            status=WorkOrderStatus.PENDING,
            reviewer_user_id=None,
            review_remark=None,
            reviewed_at=None,
            env="test",
            gmt_created=now,
            gmt_modified=now,
        ),
        event_type="SKILL_COLLABORATOR_APPLIED",
        title="pending",
        space_id=7,
        space_name="Team",
        applicant_name="Member",
        can_approve=True,
    )

    result = handler.process(
        detail=detail,
        actor_id="owner-1",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
    )

    assert result == expected
    call = repository.review_skill_editor_request.call_args.kwargs
    assert call["work_order_id"] == 11
    assert call["reviewer_user_id"] == "owner-1"
    assert call["target_status"] is WorkOrderStatus.APPROVED
    assert call["notification"].recipient_user_id == "member-1"
    assert call["notification"].event_type == "SKILL_COLLABORATOR_REVIEWED"
    assert call["notification"].content == {"text": "你共同编辑 Skill「Review Skill」的申请已通过。"}
    assert call["env"] == "test"
