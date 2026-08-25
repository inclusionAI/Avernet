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


def _service(*, actor_role=SpaceRole.MEMBER, space_type=SpaceType.TEAM):
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
    return SpaceSkillGrantService(access, repository), access, repository


def test_list_grants_returns_acl_qualifications_not_state_predictions(monkeypatch):
    service, _, repository = _service(actor_role=SpaceRole.ADMIN)
    repository.list_grants.return_value["actor_role"] = None
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_grant_service.get_current_env",
        lambda: "test",
    )

    result = service.list_grants(space_id=7, skill_id=9, actor_id="space-admin")

    assert result["actor"]["skill_role"] is None
    assert result["actor"]["permissions"] == {
        "edit_draft": False,
        "publish_draft": False,
        "delete_draft": False,
        "create_upgrade_draft": False,
        "retire_skill": False,
        "manage_grants": False,
        "transfer_owner": True,
        "request_edit_access": True,
        "takeover_lease": False,
    }
    repository.list_grants.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="space-admin", env="test"
    )


def test_owner_can_idempotently_add_manager(monkeypatch):
    service, _, repository = _service()
    repository.get_active_role.return_value = "OWNER"
    repository.add_manager.return_value = {"user_id": "manager-1", "role": "MANAGER"}
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_grant_service.get_current_env",
        lambda: "test",
    )

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


def test_manager_cannot_manage_other_grants(monkeypatch):
    service, _, repository = _service()
    repository.get_active_role.return_value = "MANAGER"
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_grant_service.get_current_env",
        lambda: "test",
    )

    with pytest.raises(SpaceSkillGrantForbiddenError):
        service.remove_manager(
            space_id=7,
            skill_id=9,
            actor_id="manager-1",
            manager_user_id="manager-2",
        )

    repository.remove_manager.assert_not_called()


def test_space_admin_transfer_requires_audit_reason(monkeypatch):
    service, _, repository = _service(actor_role=SpaceRole.ADMIN)
    repository.get_active_role.return_value = None
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_grant_service.get_current_env",
        lambda: "test",
    )

    with pytest.raises(SpaceSkillGrantReasonRequiredError):
        service.transfer_owner(
            space_id=7,
            skill_id=9,
            actor_id="space-admin",
            new_owner_user_id="member-2",
            reason=None,
        )

    repository.transfer_owner.assert_not_called()


def test_owner_transfer_does_not_require_admin_reason(monkeypatch):
    service, _, repository = _service()
    repository.get_active_role.return_value = "OWNER"
    repository.transfer_owner.return_value = {
        "owner": {"user_id": "member-2", "role": "OWNER"},
        "managers": [],
        "actor_role": None,
    }
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_grant_service.get_current_env",
        lambda: "test",
    )

    result = service.transfer_owner(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        new_owner_user_id="member-2",
        reason=None,
    )

    assert result["owner"]["user_id"] == "member-2"
    repository.transfer_owner.assert_called_once_with(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        new_owner_user_id="member-2",
        reason=None,
        env="test",
    )
