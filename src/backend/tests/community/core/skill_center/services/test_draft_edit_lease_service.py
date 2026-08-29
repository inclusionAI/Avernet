"""Behaviour tests for the Draft Edit Lease Service API seam."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseForbiddenError,
    DraftEditLeaseNotFoundError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.skill_center.services.draft_edit_lease_service import (
    DraftEditLeaseService,
)
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
)


def _service(*, space_type=SpaceType.TEAM, role="MANAGER", access_error=None):
    access = MagicMock()
    if access_error is not None:
        access.require_space_member.side_effect = access_error
    else:
        access.require_space_member.return_value = (
            SimpleNamespace(space_type=space_type),
            SimpleNamespace(role="MEMBER"),
        )
    grants = MagicMock()
    if role is None:
        grants.require_editor.side_effect = SpaceSkillGrantForbiddenError()
    else:
        grants.require_editor.return_value = role
    leases = MagicMock()
    service = DraftEditLeaseService(access, grants, leases, lambda: "test")
    return service, grants, leases


def test_personal_space_reports_that_a_lease_is_not_required():
    service, grants, leases = _service(space_type=SpaceType.PERSONAL)

    result = service.get_lease(space_id=7, skill_id=9, actor_id="owner-1")

    assert result == {
        "required": False,
        "state": "NOT_REQUIRED",
        "holder_user_id": None,
        "fencing_token": None,
    }
    grants.require_editor.assert_not_called()
    leases.get_lease.assert_called_once_with(space_id=7, skill_id=9, env="test")


def test_team_read_requires_an_editable_draft_and_returns_available():
    service, _, leases = _service()
    leases.get_lease.return_value = None

    result = service.get_lease(space_id=7, skill_id=9, actor_id="manager-1")

    assert result["state"] == "FREE"
    leases.get_lease.assert_called_once_with(space_id=7, skill_id=9, env="test")


def test_personal_not_required_still_rejects_a_missing_draft():
    service, _, leases = _service(space_type=SpaceType.PERSONAL)
    leases.get_lease.side_effect = DraftEditLeaseNotFoundError()

    with pytest.raises(DraftEditLeaseNotFoundError):
        service.get_lease(space_id=7, skill_id=999, actor_id="owner-1")


def test_team_manager_acquires_a_new_fencing_token_through_the_grant_seam():
    service, grants, leases = _service(role="MANAGER")
    leases.acquire.return_value = {
        "holder_user_id": "manager-1",
        "fencing_token": 41,
    }

    result = service.acquire(space_id=7, skill_id=9, actor_id="manager-1")

    assert result == {
        "required": True,
        "state": "HELD_BY_ME",
        "holder_user_id": "manager-1",
        "fencing_token": 41,
    }
    grants.require_editor.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="manager-1"
    )
    leases.acquire.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="manager-1", env="test"
    )


def test_active_member_without_a_skill_grant_cannot_acquire():
    service, _, leases = _service(role=None)

    with pytest.raises(DraftEditLeaseForbiddenError):
        service.acquire(space_id=7, skill_id=9, actor_id="member-1")

    leases.acquire.assert_not_called()


def test_non_member_is_classified_as_lease_forbidden():
    service, _, leases = _service(access_error=SpaceAccessDeniedError())

    with pytest.raises(DraftEditLeaseForbiddenError):
        service.acquire(space_id=7, skill_id=9, actor_id="outsider")

    leases.acquire.assert_not_called()


def test_holder_release_requires_the_current_fencing_token():
    service, _, leases = _service(role="OWNER")
    leases.release.return_value = {
        "holder_user_id": None,
        "fencing_token": 42,
    }

    result = service.release(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        fencing_token=41,
    )

    assert result["state"] == "FREE"
    assert result["fencing_token"] is None
    leases.release.assert_called_once_with(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        fencing_token=41,
        env="test",
    )


def test_owner_or_manager_can_take_over_and_receive_the_new_token():
    service, _, leases = _service(role="OWNER")
    leases.takeover.return_value = {
        "holder_user_id": "owner-1",
        "fencing_token": 99,
    }

    result = service.takeover(space_id=7, skill_id=9, actor_id="owner-1")

    assert result["state"] == "HELD_BY_ME"
    assert result["fencing_token"] == 99


def test_database_failure_is_never_translated_to_success():
    service, _, leases = _service(role="OWNER")
    leases.takeover.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.takeover(space_id=7, skill_id=9, actor_id="owner-1")


def test_service_conforms_to_the_public_protocol():
    service, _, _ = _service()

    assert isinstance(service, DraftEditLeaseServiceProtocol)
