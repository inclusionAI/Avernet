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
from agentclaw.community.plugin_api.staff_dept import (
    StaffProfileInfo,
    StaffProfileLookupError,
)


def _service():
    access = MagicMock()
    access.require_space_member.return_value = (
        MagicMock(space_type="TEAM"),
        MagicMock(role="MEMBER"),
    )
    repository = MagicMock()
    repository.list_skills.return_value = (0, [])
    staff_dept = MagicMock()
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="owner-1", nick_name=None
    )
    return (
        SpaceSkillQueryService(access, repository, staff_dept),
        access,
        repository,
        staff_dept,
    )


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [("  form  ", "form"), ("   ", None), (None, None)],
)
def test_list_space_skills_authorizes_normalizes_and_paginates(
    monkeypatch, keyword, expected
):
    service, access, repository, _ = _service()
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_query_service.get_current_env",
        lambda: "test",
    )

    result = service.list_space_skills(
        space_id=7,
        actor_id="member-1",
        keyword=keyword,
        page=3,
        page_size=20,
    )

    assert result == (0, [])
    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    repository.list_skills.assert_called_once_with(
        space_id=7,
        actor_id="member-1",
        env="test",
        keyword=expected,
        offset=40,
        limit=20,
    )


@pytest.mark.parametrize("error", [SpaceAccessDeniedError, SpaceNotFoundError])
def test_list_space_skills_propagates_access_errors_without_querying_repository(error):
    service, access, repository, _ = _service()
    access.require_space_member.side_effect = error("denied")

    with pytest.raises(error):
        service.list_space_skills(
            space_id=7,
            actor_id="outsider",
            keyword=None,
            page=1,
            page_size=20,
        )

    repository.list_skills.assert_not_called()


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

    service, access, repository, staff_dept = _service()
    access.require_space_member.return_value = (
        MagicMock(space_type=space_type),
        MagicMock(role="MEMBER"),
    )
    timestamp = datetime(2026, 8, 20, 3, 40)
    repository.list_skills.return_value = (
        1,
        [
            {
                "id": 1,
                "skill_uuid": "skill-1",
                "name": "Example",
                "description": None,
                "status": "DEVELOPING",
                "source_type": "FOLDER",
                "draft_status": "EDITING",
                "draft_target_version": 1,
                "draft_description": "Draft example",
                "draft_locator": (
                    "draft://11111111-1111-4111-8111-111111111111/"
                    "v1/22222222-2222-4222-8222-222222222222"
                ),
                "draft_source_kind": "FOLDER",
                "source_repo_url": None,
                "source_branch": None,
                "source_subdir": None,
                "source_commit_sha": None,
                "offline_at": None,
                "offline_by": None,
                "space_type": space_type,
                "current_user_skill_role": role,
                "owner_user_id": "owner-1",
                "owner_display_name": "Owner One",
                "lease_holder_user_id": "manager-2" if space_type == "TEAM" else None,
                "lease_holder_display_name": "Manager Two"
                if space_type == "TEAM"
                else None,
                "latest_version_id": None,
                "latest_version_ordinal": None,
                "latest_sc_version_number": None,
                "latest_published_at": None,
                "active_attempt_id": None,
                "active_attempt_target_version": None,
                "active_attempt_status": None,
                "pending_request_id": None,
                "pending_request_no": None,
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
        page=1,
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
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="owner-1")


def test_list_space_skills_uses_current_staff_profile_for_owner_display_name(
    monkeypatch,
):
    from datetime import datetime

    service, _, repository, staff_dept = _service()
    staff_dept.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="165528", nick_name="  卷瓜  "
    )
    timestamp = datetime(2026, 9, 2, 9, 0)
    repository.list_skills.return_value = (
        1,
        [
            _record(
                timestamp=timestamp,
                owner_user_id="165528",
                owner_display_name=None,
            )
        ],
    )
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_query_service.get_current_env",
        lambda: "test",
    )

    _, records = service.list_space_skills(
        space_id=7, actor_id="member-1", keyword=None, page=1, page_size=20
    )

    assert records[0]["owner"] == {"user_id": "165528", "display_name": "卷瓜"}
    staff_dept.get_profile_by_work_no.assert_called_once_with(work_no="165528")


def test_list_space_skills_preserves_owner_snapshot_when_profile_lookup_fails(
    monkeypatch,
):
    from datetime import datetime

    service, _, repository, staff_dept = _service()
    timestamp = datetime(2026, 9, 2, 9, 0)
    repository.list_skills.return_value = (
        1,
        [
            _record(
                timestamp=timestamp,
                owner_user_id="165528",
                owner_display_name="旧名称",
            )
        ],
    )
    staff_dept.get_profile_by_work_no.side_effect = StaffProfileLookupError(
        "unavailable"
    )
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_query_service.get_current_env",
        lambda: "test",
    )

    _, records = service.list_space_skills(
        space_id=7, actor_id="member-1", keyword=None, page=1, page_size=20
    )

    assert records[0]["owner"] == {"user_id": "165528", "display_name": "旧名称"}


def _record(*, timestamp, owner_user_id: str, owner_display_name: str | None):
    return {
        "id": 1,
        "skill_uuid": "skill-1",
        "name": "Example",
        "description": None,
        "status": "DEVELOPING",
        "source_type": "FOLDER",
        "draft_status": "EDITING",
        "draft_target_version": 1,
        "draft_description": "Draft example",
        "draft_locator": (
            "draft://11111111-1111-4111-8111-111111111111/"
            "v1/22222222-2222-4222-8222-222222222222"
        ),
        "draft_source_kind": "FOLDER",
        "source_repo_url": None,
        "source_branch": None,
        "source_subdir": None,
        "source_commit_sha": None,
        "offline_at": None,
        "offline_by": None,
        "space_type": "TEAM",
        "current_user_skill_role": "OWNER",
        "owner_user_id": owner_user_id,
        "owner_display_name": owner_display_name,
        "lease_holder_user_id": None,
        "lease_holder_display_name": None,
        "latest_version_id": None,
        "latest_version_ordinal": None,
        "latest_sc_version_number": None,
        "latest_published_at": None,
        "active_attempt_id": None,
        "active_attempt_target_version": None,
        "active_attempt_status": None,
        "pending_request_id": None,
        "pending_request_no": None,
        "gmt_created": timestamp,
        "gmt_modified": timestamp,
    }
