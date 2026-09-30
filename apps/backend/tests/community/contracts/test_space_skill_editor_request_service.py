"""Consumer contract for the Space Skill editor-request Service API."""

from unittest.mock import MagicMock

from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.work_orders.models import WorkOrderStatus
from agentclaw.community.plugin_api.staff_dept import (
    StaffProfileInfo,
    StaffProfileLookupError,
)


def test_space_skill_editor_request_service_routes_through_work_order_repository(
) -> None:
    repository = MagicMock()
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="200177", nick_name="张三"
    )
    repository.create_skill_editor_request.return_value.status = WorkOrderStatus.PENDING
    service = SpaceSkillEditorRequestService(repository, staff_dept, lambda: "test")
    assert isinstance(service, SpaceSkillEditorRequestServiceProtocol)
    result = service.create_request(
        space_id=7,
        skill_id=9,
        applicant_user_id="200177",
        reason="  maintain together  ",
    )

    assert result.status is WorkOrderStatus.PENDING
    repository.create_skill_editor_request.assert_called_once_with(
        space_id=7,
        skill_id=9,
        applicant_user_id="200177",
        applicant_name="张三",
        apply_reason="maintain together",
        env="test",
    )
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="200177")


def test_space_skill_editor_request_service_falls_back_to_work_no_when_profile_lookup_fails(
) -> None:
    repository = MagicMock()
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError(
        "directory unavailable"
    )
    repository.create_skill_editor_request.return_value.status = WorkOrderStatus.PENDING
    service = SpaceSkillEditorRequestService(repository, staff_dept, lambda: "test")

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
