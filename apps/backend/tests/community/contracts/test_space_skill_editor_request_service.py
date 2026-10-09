"""Consumer contract for the Space Skill editor-request Service API."""

from unittest.mock import MagicMock

from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.core.skill_center.editor_request_contract import (
    SkillEditorRequestAdmission,
)
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderEventCreatedResult,
    WorkOrderEventStatus,
    WorkOrderEventType,
    WorkOrderMessageTitle,
    WorkOrderStatus,
)
import pytest
from agentclaw.community.plugin_api.staff_dept import (
    StaffProfileInfo,
    StaffProfileLookupError,
)


def test_space_skill_editor_request_service_routes_through_work_order_repository() -> (
    None
):
    repository = MagicMock()
    policy_repository = MagicMock()
    work_orders = MagicMock()
    policy_repository.inspect_editor_request.return_value = SkillEditorRequestAdmission(
        auto_approve=False, skill_name="Skill"
    )
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="200177", nick_name="张三"
    )
    repository.create_skill_editor_request.return_value.status = WorkOrderStatus.PENDING
    service = SpaceSkillEditorRequestService(
        repository, policy_repository, work_orders, staff_dept, lambda: "test"
    )
    assert isinstance(service, SpaceSkillEditorRequestServiceProtocol)
    result = service.create_request(
        space_id=7,
        skill_id=9,
        applicant_user_id="200177",
        reason="  maintain together  ",
    )

    assert result.status is WorkOrderStatus.PENDING
    policy_repository.inspect_editor_request.assert_called_once_with(
        space_id=7, skill_id=9, applicant_user_id="200177", env="test"
    )
    repository.create_skill_editor_request.assert_called_once_with(
        space_id=7,
        skill_id=9,
        applicant_user_id="200177",
        applicant_name="张三",
        apply_reason="maintain together",
        env="test",
    )
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="200177")


def test_space_skill_editor_request_service_falls_back_to_work_no_when_profile_lookup_fails() -> (
    None
):
    repository = MagicMock()
    policy_repository = MagicMock()
    work_orders = MagicMock()
    policy_repository.inspect_editor_request.return_value = SkillEditorRequestAdmission(
        auto_approve=False, skill_name="Skill"
    )
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError(
        "directory unavailable"
    )
    repository.create_skill_editor_request.return_value.status = WorkOrderStatus.PENDING
    service = SpaceSkillEditorRequestService(
        repository, policy_repository, work_orders, staff_dept, lambda: "test"
    )

    service.create_request(
        space_id=7,
        skill_id=9,
        applicant_user_id="200177",
        reason="maintain together",
    )

    repository.create_skill_editor_request.assert_called_once_with(
        space_id=7,
        skill_id=9,
        applicant_user_id="200177",
        applicant_name="200177",
        apply_reason="maintain together",
        env="test",
    )


def test_space_skill_editor_request_service_routes_owner_policy() -> None:
    repository = MagicMock()
    policy_repository = MagicMock()
    policy_repository.get_editor_approval_policy.return_value = False
    policy_repository.update_editor_approval_policy.return_value = True
    service = SpaceSkillEditorRequestService(
        repository, policy_repository, MagicMock(), MagicMock(), lambda: "test"
    )

    assert (
        service.get_approval_policy(space_id=7, skill_id=9, actor_id="owner") is False
    )
    assert (
        service.update_approval_policy(
            space_id=7,
            skill_id=9,
            actor_id="owner",
            auto_approve_editor_requests=True,
        )
        is True
    )
    policy_repository.get_editor_approval_policy.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="owner", env="test"
    )
    policy_repository.update_editor_approval_policy.assert_called_once_with(
        space_id=7,
        skill_id=9,
        actor_id="owner",
        auto_approve_editor_requests=True,
        env="test",
    )


def test_enabled_policy_uses_trusted_auto_work_order_and_no_owner_todo() -> None:
    repository = MagicMock()
    skill_repository = MagicMock()
    skill_repository.inspect_editor_request.return_value = SkillEditorRequestAdmission(
        auto_approve=True, skill_name="Review Skill"
    )
    work_orders = MagicMock()
    work_orders.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=91,
        work_order_no="WO-91",
        notification_ids=[12],
        status=WorkOrderEventStatus.APPROVED,
    )
    staff_dept = MagicMock()
    service = SpaceSkillEditorRequestService(
        repository, skill_repository, work_orders, staff_dept, lambda: "test"
    )

    result = service.create_request(
        space_id=7, skill_id=9, applicant_user_id="200177", reason="共同维护"
    )

    assert (result.work_order_id, result.status) == (91, WorkOrderStatus.APPROVED)
    work_orders.create_work_order_event.assert_called_once_with(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
        biz_id="9",
        event_type=WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value,
        applicant_user_id="200177",
        approver_user_ids=[],
        recipient_user_ids=["200177"],
        title=WorkOrderMessageTitle.SKILL_COLLABORATOR_PENDING.value,
        content=None,
        apply_reason="共同维护",
        biz_data={"space_id": 7, "skill_id": 9, "skill_name": "Review Skill"},
        actor_id="200177",
    )
    repository.create_skill_editor_request.assert_not_called()
    staff_dept.get_profile_by_work_no.assert_not_called()


def test_enabled_policy_does_not_report_failed_auto_as_success() -> None:
    skill_repository = MagicMock()
    skill_repository.inspect_editor_request.return_value = SkillEditorRequestAdmission(
        auto_approve=True, skill_name="Review Skill"
    )
    work_orders = MagicMock()
    work_orders.create_work_order_event.return_value = WorkOrderEventCreatedResult(
        event_category=NotificationCategory.APPROVAL,
        work_order_id=92,
        work_order_no="WO-92",
        notification_ids=[13],
        status=WorkOrderEventStatus.FAILED,
    )
    service = SpaceSkillEditorRequestService(
        MagicMock(), skill_repository, work_orders, MagicMock(), lambda: "test"
    )

    with pytest.raises(
        WorkOrderSkillEditorRequestNotAllowedError, match="did not complete"
    ):
        service.create_request(
            space_id=7, skill_id=9, applicant_user_id="200177", reason="共同维护"
        )
