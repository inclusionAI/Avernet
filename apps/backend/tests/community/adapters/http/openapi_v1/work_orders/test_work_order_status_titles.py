"""Nonterminal and failure titles do not masquerade as generic notifications."""

import pytest

from agentclaw.community.adapters.http.openapi_v1.work_orders.converter import (
    display_title,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderApprovalMode,
    WorkOrderStatus,
)


@pytest.mark.parametrize(
    "biz_type,subject",
    [
        ("SPACE_JOIN", "空间加入申请"),
        ("BOT_COLLABORATOR", "Bot 共同编辑申请"),
        ("SKILL_COLLABORATOR", "Skill 共同编辑申请"),
        ("BOT_FRIEND", "好友申请"),
        ("FUTURE_MODULE", "工单"),
    ],
)
@pytest.mark.parametrize(
    "status,outcome",
    [
        (WorkOrderStatus.PROCESSING, "处理中"),
        (WorkOrderStatus.FAILED, "处理失败"),
    ],
)
@pytest.mark.parametrize(
    "mode,suffix",
    [
        (WorkOrderApprovalMode.AUTO, "（自动审批）"),
        (WorkOrderApprovalMode.MANUAL, ""),
    ],
)
def test_status_title_uses_business_state_and_persisted_mode(
    biz_type, subject, status, outcome, mode, suffix
):
    assert (
        display_title(
            "新的系统通知", biz_type=biz_type, status=status, approval_mode=mode
        )
        == f"{subject}{outcome}{suffix}"
    )
