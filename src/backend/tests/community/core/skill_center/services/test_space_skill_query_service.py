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
    ("space_type", "role", "can_edit", "can_grant", "can_apply_edit"),
    [
        ("PERSONAL", "OWNER", True, False, False),
        ("TEAM", "OWNER", True, True, False),
        ("TEAM", "MANAGER", True, False, False),
        ("TEAM", None, False, False, True),
        ("PERSONAL", None, False, False, False),
    ],
)
def test_list_space_skills_derives_explicit_ui_permissions(
    monkeypatch, space_type, role, can_edit, can_grant, can_apply_edit
):
    from datetime import datetime

    service, _, repository = _service()
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
    assert records[0]["can_edit"] is can_edit
    assert records[0]["can_grant"] is can_grant
    assert records[0]["can_apply_edit"] is can_apply_edit
    expected_state = "HELD_BY_OTHER" if space_type == "TEAM" else "NOT_REQUIRED"
    assert records[0]["lease_summary"] == {
        "required": space_type == "TEAM",
        "state": expected_state,
        "holder_user_id": "manager-2" if space_type == "TEAM" else None,
    }
    assert "fencing_token" not in records[0]["lease_summary"]
