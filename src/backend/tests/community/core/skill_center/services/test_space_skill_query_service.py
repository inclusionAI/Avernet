"""Unit tests for the Space Skill query service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skill_center.services.space_skill_query_service import (
    SpaceSkillQueryService,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)


def _service():
    access = MagicMock()
    access.require_space_member.return_value = (
        MagicMock(space_type="TEAM"),
        MagicMock(role="MEMBER"),
    )
    repository = MagicMock()
    repository.list_space_skills.return_value = (0, [])
    return SpaceSkillQueryService(access, repository), access, repository


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [("  form  ", "form"), ("   ", None), (None, None)],
)
def test_list_space_skills_authorizes_normalizes_and_paginates(
    monkeypatch, keyword, expected
):
    service, access, repository = _service()
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_query_service.get_current_env",
        lambda: "test",
    )

    result = service.list_space_skills(
        space_id=7,
        actor_id="member-1",
        keyword=keyword,
        page_no=3,
        page_size=20,
    )

    assert result == (0, [])
    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    repository.list_space_skills.assert_called_once_with(
        space_id=7,
        actor_id="member-1",
        env="test",
        keyword=expected,
        offset=40,
        limit=20,
    )


@pytest.mark.parametrize("error", [SpaceAccessDeniedError, SpaceNotFoundError])
def test_list_space_skills_propagates_access_errors_without_querying_repository(error):
    service, access, repository = _service()
    access.require_space_member.side_effect = error("denied")

    with pytest.raises(error):
        service.list_space_skills(
            space_id=7,
            actor_id="outsider",
            keyword=None,
            page_no=1,
            page_size=20,
        )

    repository.list_space_skills.assert_not_called()


@pytest.mark.parametrize(
    ("space_type", "role", "can_edit", "can_manage", "can_request"),
    [
        ("PERSONAL", "OWNER", True, True, False),
        ("TEAM", "OWNER", True, True, False),
        ("TEAM", "MANAGER", True, False, False),
        ("TEAM", None, False, False, True),
        ("PERSONAL", None, False, False, False),
    ],
)
def test_list_space_skills_derives_actor_permissions_and_lease_summary(
    monkeypatch, space_type, role, can_edit, can_manage, can_request
):
    from datetime import datetime

    service, access, repository = _service()
    access.require_space_member.return_value = (
        MagicMock(space_type=space_type),
        MagicMock(role="MEMBER"),
    )
    timestamp = datetime(2026, 8, 20, 3, 40)
    repository.list_space_skills.return_value = (
        1,
        [
            {
                "id": 1,
                "skill_uuid": "skill-1",
                "name": "Example",
                "description": None,
                "status": "DEVELOPING",
                "draft_status": "EDITING",
                "space_type": space_type,
                "current_user_skill_role": role,
                "lease_holder_user_id": "manager-2" if space_type == "TEAM" else None,
                "lease_holder_display_name": "Manager Two" if space_type == "TEAM" else None,
                "gmt_created": timestamp,
                "gmt_modified": timestamp,
            }
        ],
    )
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_query_service.get_current_env",
        lambda: "test",
    )

    total, records = service.list_space_skills(
        space_id=7,
        actor_id="member-1",
        keyword=None,
        page_no=1,
        page_size=20,
    )

    assert total == 1
    actor = records[0]["actor"]
    assert actor["skill_role"] == role
    assert actor["permissions"]["edit_draft"] is can_edit
    assert actor["permissions"]["manage_grants"] is can_manage
    assert actor["permissions"]["request_edit_access"] is can_request
    expected_state = "HELD_BY_OTHER" if space_type == "TEAM" else "NOT_REQUIRED"
    assert records[0]["lease_summary"] == {
        "required": space_type == "TEAM",
        "state": expected_state,
        "holder_user_id": "manager-2" if space_type == "TEAM" else None,
        "holder_display_name": "Manager Two" if space_type == "TEAM" else None,
    }
    assert "fencing_token" not in records[0]["lease_summary"]
