"""Endpoint coverage for Spaces, Space members and market favorites.

Happy paths seed through the real services behind the same injector the
endpoint resolves, so every row a case relies on is written the way
production writes it. The uniform error path is the principal seam: a
``user_id`` naming someone other than the verified caller answers 403 on
every user-scoped operation, and the internal batch query — which carries
no principal — errors on an invalid body instead.
"""

from __future__ import annotations

import time
from datetime import datetime

import jwt

from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_skill_query_service import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketSource,
)
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.work_orders.models import WorkOrderRecord, WorkOrderStatus
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_failing_method,
    bind_overrides,
    endpoint_test,
)

_USER_ID = "spaces-endpoint-user"
_MEMBER_ID = "spaces-endpoint-member"
_SIGNING_KEY = "spaces-endpoint-secret-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "spaces-endpoint-test",
                    "subject": {
                        "id": _USER_ID,
                        "username": "spaces-endpoint-user@example.com",
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_public_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_personal_space(world) -> None:
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).initialize_personal(user_id=_USER_ID)


def _seed_team_space(world) -> None:
    """A team Space created by the acting user — Space id 1 in the fresh DB."""
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="Endpoint Team", creator_id=_USER_ID
    )


def _seed_team_with_member(world) -> None:
    _seed_team_space(world)
    world.get(SpaceMemberServiceProtocol).add_member(
        space_id=1,
        actor_id=_USER_ID,
        user_id=_MEMBER_ID,
        role=SpaceRole.MEMBER,
    )


def _seed_team_with_favorite(world) -> None:
    _seed_team_space(world)
    world.get(MarketFavoriteServiceProtocol).add(
        space_id=1,
        actor_id=_USER_ID,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-endpoint-1",
    )


def _seed_space_skills(world) -> None:
    _enable_public_auth(world)

    def _list_space_skills(_self, **_kwargs):
        return 1, [
            {
                "id": 10001,
                "skill_uuid": "skill-endpoint-uuid",
                "name": "Endpoint Skill",
                "description": "A Skill for endpoint coverage.",
                "status": "DEVELOPING",
                "draft_status": "EDITING",
                "space_type": "TEAM",
                "current_user_skill_role": "OWNER",
                "can_edit": True,
                "can_grant": True,
                "can_apply_edit": False,
                "lease_summary": {
                    "required": True,
                    "state": "AVAILABLE",
                    "holder_user_id": None,
                },
                "gmt_created": datetime(2026, 8, 20, 3, 30),
                "gmt_modified": datetime(2026, 8, 20, 3, 40),
            }
        ]

    bind_overrides(
        world,
        SpaceSkillQueryServiceProtocol,
        {"list_space_skills": _list_space_skills},
    )


def _seed_space_skill_error(world) -> None:
    _enable_public_auth(world)
    bind_failing_method(
        world,
        SpaceSkillQueryServiceProtocol,
        "list_space_skills",
        SpaceAccessDeniedError("space membership required"),
    )


def _seed_space_skill_grants(world) -> None:
    _enable_public_auth(world)

    def _grants(owner_id: str = _USER_ID):
        return {
            "owner": {"user_id": owner_id, "role": "OWNER"},
            "managers": [],
            "actor": {
                "skill_role": "OWNER" if owner_id == _USER_ID else None,
                "permissions": {
                    "edit_draft": owner_id == _USER_ID,
                    "publish_draft": owner_id == _USER_ID,
                    "delete_draft": owner_id == _USER_ID,
                    "create_upgrade_draft": owner_id == _USER_ID,
                    "retire_skill": owner_id == _USER_ID,
                    "manage_grants": owner_id == _USER_ID,
                    "transfer_owner": owner_id == _USER_ID,
                    "request_edit_access": owner_id != _USER_ID,
                    "takeover_lease": owner_id == _USER_ID,
                },
            },
        }

    bind_overrides(
        world,
        SpaceSkillGrantServiceProtocol,
        {
            "list_grants": lambda _self, **_kwargs: _grants(),
            "add_manager": lambda _self, **kwargs: {
                "user_id": kwargs["manager_user_id"],
                "role": "MANAGER",
            },
            "remove_manager": lambda _self, **kwargs: {
                "user_id": kwargs["manager_user_id"],
                "role": "MANAGER",
            },
            "transfer_owner": lambda _self, **kwargs: _grants(
                kwargs["new_owner_user_id"]
            ),
        },
    )


def _seed_space_skill_editor_request(world) -> None:
    _enable_public_auth(world)

    def _create_request(_self, **kwargs):
        now = datetime(2026, 8, 26, 8, 0)
        return WorkOrderRecord(
            id=91,
            work_order_no="WO-91",
            biz_type="SKILL_COLLABORATOR",
            biz_id=str(kwargs["skill_id"]),
            applicant_user_id=kwargs["applicant_user_id"],
            apply_reason=kwargs["reason"],
            status=WorkOrderStatus.PENDING,
            reviewer_user_id=None,
            review_remark=None,
            reviewed_at=None,
            env="test",
            gmt_created=now,
            gmt_modified=now,
        )

    bind_overrides(
        world,
        SpaceSkillEditorRequestServiceProtocol,
        {"create_request": _create_request},
    )


def _seed_draft_edit_lease(world) -> None:
    _enable_public_auth(world)

    def _held(token: int):
        return {
            "required": True,
            "state": "HELD_BY_SELF",
            "holder_user_id": _USER_ID,
            "fencing_token": token,
        }

    bind_overrides(
        world,
        DraftEditLeaseServiceProtocol,
        {
            "get_lease": lambda _self, **_kwargs: _held(7),
            "acquire": lambda _self, **_kwargs: _held(8),
            "release": lambda _self, **_kwargs: {
                "required": True,
                "state": "AVAILABLE",
                "holder_user_id": None,
                "fencing_token": None,
            },
            "takeover": lambda _self, **_kwargs: _held(9),
        },
    )


def _mismatched_user(path_params: dict | None = None, json_body: dict | None = None):
    """The uniform error case: naming someone other than the caller is a 403."""
    return CaseInput(
        path_params=path_params or {},
        query_params={"user_id": "someone-else"},
        json_body=json_body,
        headers=_principal_headers(),
    )


# ── Space Skill Grant management ─────────────────────────────────────────────


# ── Draft Edit Lease management ──────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    scenario="happy",
    seed=_seed_draft_edit_lease,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"state": "HELD_BY_SELF", "fencing_token": 7}},
    ),
)
def get_draft_edit_lease_happy():
    """The current holder can re-read the live fencing token."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user({"space_id": 1, "skill_id": 9}),
    expect=ExpectError(status=403),
)
def get_draft_edit_lease_wrong_user():
    """A mismatched explicit actor is refused."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    scenario="happy",
    seed=_seed_draft_edit_lease,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"state": "HELD_BY_SELF", "fencing_token": 8}},
    ),
)
def acquire_draft_edit_lease_happy():
    """Acquire returns the newly generated token."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user({"space_id": 1, "skill_id": 9}),
    expect=ExpectError(status=403),
)
def acquire_draft_edit_lease_wrong_user():
    """A mismatched actor cannot acquire."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    scenario="happy",
    seed=_seed_draft_edit_lease,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID, "fencing_token": 7},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"data": {"state": "AVAILABLE"}}
    ),
)
def release_draft_edit_lease_happy():
    """Release consumes the exact current fencing token."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": "someone-else", "fencing_token": 7},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=403),
)
def release_draft_edit_lease_wrong_user():
    """A mismatched actor is refused before token validation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover",
    scenario="happy",
    seed=_seed_draft_edit_lease,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"state": "HELD_BY_SELF", "fencing_token": 9}},
    ),
)
def takeover_draft_edit_lease_happy():
    """Takeover returns a new fencing token."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user({"space_id": 1, "skill_id": 9}),
    expect=ExpectError(status=403),
)
def takeover_draft_edit_lease_wrong_user():
    """A mismatched actor cannot take over."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests",
    scenario="happy",
    seed=_seed_space_skill_editor_request,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
        json_body={"reason": "maintain together"},
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={"data": {"work_order_id": 91, "status": "PENDING"}},
    ),
)
def create_space_skill_editor_request_happy():
    """An eligible member receives the pending Work Order identity."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        {"space_id": 1, "skill_id": 9},
        {"reason": "maintain together"},
    ),
    expect=ExpectError(status=403),
)
def create_space_skill_editor_request_wrong_user():
    """A mismatched actor is refused before Skill policy executes."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants",
    scenario="happy",
    seed=_seed_space_skill_grants,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"owner": {"user_id": _USER_ID}}},
    ),
)
def list_space_skill_grants_happy():
    """An active Space member receives the current Grant set."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user({"space_id": 1, "skill_id": 9}),
    expect=ExpectError(status=403),
)
def list_space_skill_grants_wrong_user():
    """The explicit acting user remains bound to the verified principal."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    scenario="happy",
    seed=_seed_space_skill_grants,
    input=CaseInput(
        path_params={
            "space_id": 1,
            "skill_id": 9,
            "manager_user_id": _MEMBER_ID,
        },
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"user_id": _MEMBER_ID, "role": "MANAGER"}},
    ),
)
def add_space_skill_manager_happy():
    """The Owner command returns the resulting MANAGER Grant."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        {"space_id": 1, "skill_id": 9, "manager_user_id": _MEMBER_ID}
    ),
    expect=ExpectError(status=403),
)
def add_space_skill_manager_wrong_user():
    """A mismatched actor is refused before Grant policy executes."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    scenario="happy",
    seed=_seed_space_skill_grants,
    input=CaseInput(
        path_params={
            "space_id": 1,
            "skill_id": 9,
            "manager_user_id": _MEMBER_ID,
        },
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"user_id": _MEMBER_ID, "role": "MANAGER"}},
    ),
)
def remove_space_skill_manager_happy():
    """An idempotent removal returns the addressed MANAGER Grant identity."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        {"space_id": 1, "skill_id": 9, "manager_user_id": _MEMBER_ID}
    ),
    expect=ExpectError(status=403),
)
def remove_space_skill_manager_wrong_user():
    """A mismatched actor cannot remove a Grant."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer",
    scenario="happy",
    seed=_seed_space_skill_grants,
    input=CaseInput(
        path_params={"space_id": 1, "skill_id": 9},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
        json_body={"new_owner_user_id": _MEMBER_ID},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"owner": {"user_id": _MEMBER_ID}}},
    ),
)
def transfer_space_skill_owner_happy():
    """The transfer response identifies the new unique Owner."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        {"space_id": 1, "skill_id": 9},
        {"new_owner_user_id": _MEMBER_ID},
    ),
    expect=ExpectError(status=403),
)
def transfer_space_skill_owner_wrong_user():
    """A mismatched actor cannot transfer ownership."""


# ── GET /openapi/v1/bots/spaces/{space_id}/skills ────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/skills",
    scenario="happy",
    seed=_seed_space_skills,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"total": 1, "items": [{"skill_id": "10001"}]},
        },
    ),
)
def list_space_skills_happy():
    """A space member receives the paged Skill card projection."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/skills",
    scenario="membership_required",
    seed=_seed_space_skill_error,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=403),
)
def list_space_skills_membership_required():
    """A caller who is not a member is refused before the query result."""


# ── GET /openapi/v1/bots/spaces ───────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces",
    scenario="happy",
    seed=_seed_personal_space,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"total": 1}}
    ),
)
def list_spaces_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(),
    expect=ExpectError(status=403),
)
def list_spaces_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/personal/initialize ──────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/personal/initialize",
    scenario="happy",
    seed=_enable_public_auth,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"created": True}}
    ),
)
def initialize_personal_space_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/personal/initialize",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(),
    expect=ExpectError(status=403),
)
def initialize_personal_space_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/create ───────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/create",
    scenario="happy",
    seed=_enable_public_auth,
    input=CaseInput(
        query_params={"user_id": _USER_ID},
        json_body={"space_name": "Endpoint Team"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"space_name": "Endpoint Team", "current_user_role": "ADMIN"},
        },
    ),
)
def create_team_space_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/create",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(json_body={"space_name": "Endpoint Team"}),
    expect=ExpectError(status=403),
)
def create_team_space_wrong_user():
    """The framework owns invocation."""


# ── GET /openapi/v1/bots/spaces/{space_id}/members ────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/members",
    scenario="happy",
    seed=_seed_team_space,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"total": 1}}
    ),
)
def list_space_members_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/spaces/{space_id}/members",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(path_params={"space_id": 1}),
    expect=ExpectError(status=403),
)
def list_space_members_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/{space_id}/members ───────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/members",
    scenario="happy",
    seed=_seed_team_space,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={"member_user_id": _MEMBER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"user_id": _MEMBER_ID, "role": "MEMBER"},
        },
    ),
)
def add_space_member_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/members",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"space_id": 1}, json_body={"member_user_id": _MEMBER_ID}
    ),
    expect=ExpectError(status=403),
)
def add_space_member_wrong_user():
    """The framework owns invocation."""


# ── DELETE /openapi/v1/bots/spaces/{space_id}/members/{member_user_id} ────────────


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}",
    scenario="happy",
    seed=_seed_team_with_member,
    input=CaseInput(
        path_params={"space_id": 1, "member_user_id": _MEMBER_ID},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"user_id": _MEMBER_ID}}
    ),
)
def delete_space_member_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(path_params={"space_id": 1, "member_user_id": _MEMBER_ID}),
    expect=ExpectError(status=403),
)
def delete_space_member_wrong_user():
    """The framework owns invocation."""


# ── PUT /openapi/v1/bots/spaces/{space_id}/members/{member_user_id}/role ──────────


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}/role",
    scenario="happy",
    seed=_seed_team_with_member,
    input=CaseInput(
        path_params={"space_id": 1, "member_user_id": _MEMBER_ID},
        query_params={"user_id": _USER_ID},
        json_body={"role": "OWNER"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"user_id": _MEMBER_ID, "role": "ADMIN"},
        },
    ),
)
def update_space_member_role_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/spaces/{space_id}/members/{member_user_id}/role",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"space_id": 1, "member_user_id": _MEMBER_ID},
        json_body={"role": "OWNER"},
    ),
    expect=ExpectError(status=403),
)
def update_space_member_role_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/{space_id}/market-favorites ──────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites",
    scenario="happy",
    seed=_seed_team_space,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": "skill-endpoint-1",
        },
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_code": "skill-endpoint-1",
                "changed": True,
            },
        },
    ),
)
def add_market_favorite_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"space_id": 1},
        json_body={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": "skill-endpoint-1",
        },
    ),
    expect=ExpectError(status=403),
)
def add_market_favorite_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/{space_id}/market-favorites/cancel ───────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites/cancel",
    scenario="happy",
    seed=_seed_team_with_favorite,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": "skill-endpoint-1",
        },
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_code": "skill-endpoint-1",
                "changed": True,
            },
        },
    ),
)
def cancel_market_favorite_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites/cancel",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"space_id": 1},
        json_body={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": "skill-endpoint-1",
        },
    ),
    expect=ExpectError(status=403),
)
def cancel_market_favorite_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/{space_id}/market-favorites/search ───────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites/search",
    scenario="happy",
    seed=_seed_team_with_favorite,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={"market_source": "SKILLCENTER"},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"total": 1}}
    ),
)
def search_market_favorites_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites/search",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(path_params={"space_id": 1}, json_body={}),
    expect=ExpectError(status=403),
)
def search_market_favorites_wrong_user():
    """The framework owns invocation."""


# ── POST /openapi/v1/bots/spaces/{space_id}/market-favorites/status ─────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites/status",
    scenario="happy",
    seed=_seed_team_with_favorite,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        json_body={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_codes": ["skill-endpoint-1", "missing"],
        },
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"favorited_target_codes": ["skill-endpoint-1"]},
        },
    ),
)
def market_favorite_status_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/spaces/{space_id}/market-favorites/status",
    scenario="wrong_user",
    seed=_enable_public_auth,
    input=_mismatched_user(
        path_params={"space_id": 1},
        json_body={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_codes": ["skill-endpoint-1"],
        },
    ),
    expect=ExpectError(status=403),
)
def market_favorite_status_wrong_user():
    """The framework owns invocation."""


# ── POST /api/internal/spaces/personal/batch-query ───────────────────────────


@endpoint_test(
    method="POST",
    path="/api/internal/spaces/personal/batch-query",
    scenario="happy",
    seed=_seed_personal_space,
    input=CaseInput(json_body={"user_id": [_USER_ID]}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"list": [{"user_id": _USER_ID, "found": True}]},
        },
    ),
)
def batch_query_personal_spaces_happy():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/api/internal/spaces/personal/batch-query",
    scenario="empty_user_list",
    input=CaseInput(json_body={"user_id": []}),
    expect=ExpectError(status=422),
)
def batch_query_personal_spaces_empty_user_list():
    """The framework owns invocation."""


# ── POST /api/internal/spaces/{space_id}/sc-team-binding/repair ───────────────


@endpoint_test(
    method="POST",
    path="/api/internal/spaces/{space_id}/sc-team-binding/repair",
    scenario="already_bound",
    seed=_seed_team_space,
    input=CaseInput(path_params={"space_id": 1}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"space_id": 1, "status": "ALREADY_BOUND"},
        },
    ),
)
def repair_space_sc_team_binding_already_bound():
    """An idempotent retry returns the binding created with the Space."""


@endpoint_test(
    method="POST",
    path="/api/internal/spaces/{space_id}/sc-team-binding/repair",
    scenario="invalid_space_id",
    input=CaseInput(path_params={"space_id": "not-an-id"}),
    expect=ExpectError(status=422),
)
def repair_space_sc_team_binding_invalid_space_id():
    """The transport rejects an invalid path id before invoking the service."""
