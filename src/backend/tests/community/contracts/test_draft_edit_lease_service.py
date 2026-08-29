"""Consumer conformance for the Draft Edit Lease Service API."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import DraftEditLeaseConflictError
from agentclaw.community.core.skill_center.services.draft_edit_lease_service import (
    DraftEditLeaseService,
)
from agentclaw.community.core.spaces.models import SpaceType


def _consumer(*, space_type=SpaceType.TEAM):
    access = MagicMock()
    access.require_space_member.return_value = (
        SimpleNamespace(space_type=space_type),
        SimpleNamespace(role="MEMBER"),
    )
    grants = MagicMock()
    grants.require_editor.return_value = "MANAGER"
    repository = MagicMock()
    service = DraftEditLeaseService(access, grants, repository, lambda: "test")
    assert isinstance(service, DraftEditLeaseServiceProtocol)
    return service, grants, repository


def test_consumer_acquires_a_team_lease_through_the_protocol():
    service, grants, repository = _consumer()
    repository.acquire.return_value = {
        "holder_user_id": "manager-1",
        "fencing_token": 7,
    }

    result = service.acquire(space_id=3, skill_id=5, actor_id="manager-1")

    assert result["state"] == "HELD_BY_ME"
    assert result["fencing_token"] == 7
    grants.require_editor.assert_called_once_with(
        space_id=3, skill_id=5, actor_id="manager-1"
    )


def test_consumer_observes_personal_space_as_not_requiring_a_lease():
    service, grants, repository = _consumer(space_type=SpaceType.PERSONAL)

    result = service.get_lease(space_id=3, skill_id=5, actor_id="owner-1")

    assert result["state"] == "NOT_REQUIRED"
    grants.require_editor.assert_not_called()
    repository.get_lease.assert_called_once_with(space_id=3, skill_id=5, env="test")


def test_consumer_propagates_repository_conflict():
    service, _, repository = _consumer()
    repository.acquire.side_effect = DraftEditLeaseConflictError()

    with pytest.raises(DraftEditLeaseConflictError):
        service.acquire(space_id=3, skill_id=5, actor_id="manager-1")
