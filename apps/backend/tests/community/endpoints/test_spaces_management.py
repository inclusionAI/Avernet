"""Registered maintenance cases: real HTTP, DI, services and SQLite.

The test profile already configures 100000 as a super-admin and provides local
Auth/SC/staff plugins. No authentication dependency or service is replaced.
"""

from agentclaw.community.api.space_management_service import (
    SpaceManagementServiceProtocol,
)
from agentclaw.community.core.spaces.management_models import (
    InitializeTeam,
    MemberBatch,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def _seed_team(world):
    result = world.get(SpaceManagementServiceProtocol).create_team(
        InitializeTeam(
            space_name="Maintenance endpoint team",
            admin_user_id="100001",
            member_user_ids=["100002"],
        )
    )
    assert result.space.id == 1


def _seed_empty_team(world):
    _seed_team(world)
    world.get(SpaceManagementServiceProtocol).remove_members(
        1, MemberBatch(member_user_ids=["100001", "100002"])
    )


# Each tuple declares the actual request and meaningful operation-specific result.
_CASES = [
    (
        "POST",
        "/api/spaces",
        None,
        {"space_type": "TEAM", "space_name": "Standalone", "admin_user_id": "100001"},
        201,
        {
            "space": {"name": "Standalone", "created_by": "100001"},
            "member_count": 1,
            "admin_count": 1,
        },
    ),
    (
        "POST",
        "/api/spaces/initialize",
        None,
        {
            "space_type": "TEAM",
            "space_name": "Initialized",
            "admin_user_id": "100001",
            "member_user_ids": ["100002"],
        },
        201,
        {"space": {"name": "Initialized"}, "member_count": 2, "admin_count": 1},
    ),
    (
        "GET",
        "/api/spaces",
        _seed_team,
        None,
        200,
        {"total": 1, "items": [{"space": {"id": 1}, "member_count": 2}]},
    ),
    (
        "GET",
        "/api/spaces/{space_id}",
        _seed_team,
        None,
        200,
        {"space": {"id": 1}, "member_count": 2, "admin_count": 1},
    ),
    (
        "POST",
        "/api/spaces/{space_id}/update",
        _seed_team,
        {"space_name": "Renamed"},
        200,
        {
            "changed": True,
            "space": {"space": {"name": "Renamed", "updated_by": "100000"}},
        },
    ),
    (
        "DELETE",
        "/api/spaces/{space_id}",
        _seed_empty_team,
        None,
        200,
        {"space_id": 1, "deleted": True, "removed_member_count": 0},
    ),
    (
        "POST",
        "/api/spaces/{space_id}/delete-with-members",
        _seed_team,
        None,
        200,
        {"space_id": 1, "deleted": True, "removed_member_count": 2},
    ),
    (
        "GET",
        "/api/spaces/{space_id}/members",
        _seed_team,
        None,
        200,
        {
            "total": 2,
            "items": [
                {"user_id": "100001", "role": "ADMIN"},
                {"user_id": "100002", "role": "MEMBER"},
            ],
        },
    ),
    (
        "POST",
        "/api/spaces/{space_id}/members/batch-add",
        _seed_team,
        {"member_user_ids": ["100002", "100003"]},
        200,
        {
            "added_user_ids": ["100003"],
            "existing_user_ids": ["100002"],
            "reactivated_user_ids": [],
        },
    ),
    (
        "POST",
        "/api/spaces/{space_id}/members/batch-remove",
        _seed_team,
        {"member_user_ids": ["100002", "100003"]},
        200,
        {"removed_user_ids": ["100002"], "missing_user_ids": ["100003"]},
    ),
    (
        "PUT",
        "/api/spaces/{space_id}/members/roles",
        _seed_team,
        {"member_user_ids": ["100001", "100002"], "role": "ADMIN"},
        200,
        {
            "role": "ADMIN",
            "updated_user_ids": ["100002"],
            "unchanged_user_ids": ["100001"],
        },
    ),
]


def _case_body():
    """Invocation belongs to the endpoint framework, not this function."""


for method, path, seed, body, status, result in _CASES:
    endpoint_test(
        method=method,
        path=path,
        scenario="space_maintenance_super_admin_success",
        seed=seed,
        input=CaseInput(
            path_params={"space_id": 1} if "{space_id}" in path else {},
            headers={"X-User-Id": "100000"},  # LocalAuth test-profile identity only.
            json_body=body,
        ),
        expect=ExpectSuccess(
            status=status, json_contains={"code": "OK", "data": result}
        ),
    )(_case_body)
    endpoint_test(
        method=method,
        path=path,
        scenario="space_maintenance_non_admin_forbidden",
        seed=seed,
        input=CaseInput(
            path_params={"space_id": 1} if "{space_id}" in path else {},
            headers={
                "X-User-Id": "100001"
            },  # Even the Space creator is not a super-admin.
            json_body=body,
        ),
        expect=ExpectError(status=403, json_contains={"code": "SPACE_ADMIN_REQUIRED"}),
    )(_case_body)
