"""Consumer conformance for the Space Skill Grant Service API."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantNotFoundError,
)
from agentclaw.community.core.skill_center.services.space_skill_grant_service import (
    SpaceSkillGrantService,
)
from agentclaw.community.core.spaces.models import SpaceRole, SpaceType
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin, StaffProfileInfo


def _consumer():
    access = MagicMock()
    access.require_space_member.return_value = (
        SimpleNamespace(space_type=SpaceType.TEAM, created_by="owner-1"),
        SimpleNamespace(role=SpaceRole.MEMBER),
    )
    repository = MagicMock()
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="owner-1", nick_name="Owner"
    )
    service = SpaceSkillGrantService(access, repository, staff_dept, lambda: "test")
    assert isinstance(service, SpaceSkillGrantServiceProtocol)
    return service, repository


def test_consumer_reads_actor_permissions_through_the_repository_contract():
    service, repository = _consumer()
    repository.list_grants.return_value = {
        "owner": {"user_id": "owner-1", "role": "OWNER"},
        "managers": [],
        "actor_role": "OWNER",
    }

    result = service.list_grants(space_id=7, skill_id=9, actor_id="owner-1")

    assert result["actor"]["permissions"]["manage_grants"] is True
    assert result["owner"]["display_name"] == "Owner"
    repository.list_grants.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="owner-1", env="test"
    )


def test_consumer_propagates_repository_not_found_failure():
    service, repository = _consumer()
    repository.list_grants.side_effect = SpaceSkillGrantNotFoundError()

    with pytest.raises(SpaceSkillGrantNotFoundError):
        service.list_grants(space_id=7, skill_id=999, actor_id="owner-1")
