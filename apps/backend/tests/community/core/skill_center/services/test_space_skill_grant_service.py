"""Behaviour tests for the Space Skill Grant service seam."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantReasonRequiredError,
)
from agentclaw.community.core.skill_center.services.space_skill_grant_service import (
    SpaceSkillGrantService,
)
from agentclaw.community.core.spaces.models import SpaceRole, SpaceType
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileInfo,
    StaffProfileLookupError,
)


def _service(
    *, actor_role=SpaceRole.MEMBER, space_type=SpaceType.TEAM, staff_dept=None
):
    access = MagicMock()
    access.require_space_member.return_value = (
        SimpleNamespace(space_type=space_type, created_by="space-admin"),
        SimpleNamespace(role=actor_role),
    )
    repository = MagicMock()
    repository.list_grants.return_value = {
        "owner": {"user_id": "owner-1", "role": "OWNER"},
        "managers": [],
        "actor_role": None,
    }
    if staff_dept is None:
        staff_dept = MagicMock(spec=StaffDeptPlugin)
        staff_dept.get_profile_by_work_no.side_effect = (
            lambda *, work_no: StaffProfileInfo(
                work_no=work_no, nick_name=f"{work_no}-name"
            )
        )
    return (
        SpaceSkillGrantService(access, repository, staff_dept, lambda: "test"),
        access,
        repository,
    )


def test_list_grants_returns_acl_qualifications_not_state_predictions():
    service, _, repository = _service(actor_role=SpaceRole.ADMIN)
    repository.list_grants.return_value["actor_role"] = None

    result = service.list_grants(space_id=7, skill_id=9, actor_id="space-admin")

    assert result["actor"]["skill_role"] is None
    assert result["actor"]["permissions"] == {
        "edit_draft": False,
        "publish_draft": False,
        "delete_draft": False,
        "create_upgrade_draft": False,
        "offline_skill": False,
        "copy_offline_skill": False,
        "manage_grants": False,
        "transfer_owner": True,
        "request_edit_access": True,
        "takeover_lease": False,
    }
    repository.list_grants.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="space-admin", env="test"
    )
    assert result["owner"]["display_name"] == "owner-1-name"


def test_list_grants_resolves_owner_and_manager_display_names():
    service, _, repository = _service()
    repository.list_grants.return_value["managers"] = [
        {"user_id": "manager-1", "role": "MANAGER"}
    ]

    result = service.list_grants(space_id=7, skill_id=9, actor_id="owner-1")

    assert result["owner"]["display_name"] == "owner-1-name"
    assert result["managers"] == [
        {
            "user_id": "manager-1",
            "role": "MANAGER",
            "display_name": "manager-1-name",
        }
    ]


def test_list_grants_keeps_user_ids_when_profile_lookup_fails():
    staff_dept = MagicMock(spec=StaffDeptPlugin)
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError(
        "directory unavailable"
    )
    service, _, _ = _service(staff_dept=staff_dept)

    result = service.list_grants(space_id=7, skill_id=9, actor_id="owner-1")

    assert result["owner"] == {
        "user_id": "owner-1",
        "role": "OWNER",
        "display_name": None,
    }


def test_owner_can_idempotently_add_manager():
    service, _, repository = _service()
    repository.get_active_role.return_value = "OWNER"
    repository.add_manager.return_value = {"user_id": "manager-1", "role": "MANAGER"}

    result = service.add_manager(
        space_id=7, skill_id=9, actor_id="owner-1", manager_user_id="manager-1"
    )

    assert result == {"user_id": "manager-1", "role": "MANAGER"}
    repository.add_manager.assert_called_once_with(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        manager_user_id="manager-1",
        env="test",
    )


def test_manager_cannot_manage_other_grants():
    service, _, repository = _service()
    repository.get_active_role.return_value = "MANAGER"

    with pytest.raises(SpaceSkillGrantForbiddenError):
        service.remove_manager(
            space_id=7,
            skill_id=9,
            actor_id="manager-1",
            manager_user_id="manager-2",
        )

    repository.remove_manager.assert_not_called()


def test_space_admin_transfer_requires_audit_reason():
    service, _, repository = _service(actor_role=SpaceRole.ADMIN)
    repository.get_active_role.return_value = None

    with pytest.raises(SpaceSkillGrantReasonRequiredError):
        service.transfer_owner(
            space_id=7,
            skill_id=9,
            actor_id="space-admin",
            new_owner_user_id="member-2",
            reason=None,
        )

    repository.transfer_owner.assert_not_called()


def test_owner_transfer_does_not_require_admin_reason():
    service, _, repository = _service()
    repository.get_active_role.return_value = "OWNER"
    repository.transfer_owner.return_value = {
        "owner": {"user_id": "member-2", "role": "OWNER"},
        "managers": [],
        "actor_role": None,
    }

    result = service.transfer_owner(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        new_owner_user_id="member-2",
        reason=None,
        retain_previous_owner_as_manager=True,
    )

    assert result["owner"]["user_id"] == "member-2"
    repository.transfer_owner.assert_called_once_with(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        new_owner_user_id="member-2",
        reason=None,
        retain_previous_owner_as_manager=True,
        env="test",
    )


def test_personal_owner_permissions_do_not_offer_editor_request():
    service, _, repository = _service(space_type=SpaceType.PERSONAL)
    repository.list_grants.return_value["actor_role"] = "OWNER"

    result = service.list_grants(space_id=7, skill_id=9, actor_id="owner-1")

    assert result["actor"]["skill_role"] == "OWNER"
    assert result["actor"]["permissions"]["manage_grants"] is True
    assert result["actor"]["permissions"]["request_edit_access"] is False


def test_persistence_failure_is_not_translated_to_success():
    service, _, repository = _service()
    repository.get_active_role.return_value = "OWNER"
    repository.add_manager.side_effect = RuntimeError("database write failed")

    with pytest.raises(RuntimeError, match="database write failed"):
        service.add_manager(
            space_id=7,
            skill_id=9,
            actor_id="owner-1",
            manager_user_id="manager-1",
        )
