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

import jwt

from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketSource,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
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


def _mismatched_user(path_params: dict | None = None, json_body: dict | None = None):
    """The uniform error case: naming someone other than the caller is a 403."""
    return CaseInput(
        path_params=path_params or {},
        query_params={"user_id": "someone-else"},
        json_body=json_body,
        headers=_principal_headers(),
    )


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
            "data": {"space_name": "Endpoint Team", "current_user_role": "OWNER"},
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
            "data": {"user_id": _MEMBER_ID, "role": "OWNER"},
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
