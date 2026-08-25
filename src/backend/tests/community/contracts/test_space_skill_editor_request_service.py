"""Consumer contract for the Space Skill editor-request Service API."""

from unittest.mock import MagicMock

from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.core.skill_center.services.space_skill_editor_request_service import (
    SpaceSkillEditorRequestService,
)
from agentclaw.community.core.work_orders.models import WorkOrderStatus


def test_space_skill_editor_request_service_routes_through_work_order_repository(
    monkeypatch,
) -> None:
    repository = MagicMock()
    repository.create_skill_editor_request.return_value.status = WorkOrderStatus.PENDING
    service = SpaceSkillEditorRequestService(repository)
    assert isinstance(service, SpaceSkillEditorRequestServiceProtocol)
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_editor_request_service.get_current_env",
        lambda: "test",
    )

    result = service.create_request(
        space_id=7,
        skill_id=9,
        applicant_user_id="member-1",
        reason="  maintain together  ",
    )

    assert result.status is WorkOrderStatus.PENDING
    repository.create_skill_editor_request.assert_called_once_with(
        space_id=7,
        skill_id=9,
        applicant_user_id="member-1",
        applicant_name="member-1",
        apply_reason="maintain together",
        env="test",
    )
