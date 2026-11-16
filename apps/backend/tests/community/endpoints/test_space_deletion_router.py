"""Endpoint coverage for the public team-space deletion route.

Happy path seeds a real empty Team Space through the same services the
endpoint resolves; the error path is the uniform principal seam: a
``user_id`` naming someone other than the verified caller answers 403 on
every user-scoped operation.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
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

_USER_ID = "spaces-deletion-endpoint-user"
_MEMBER_ID = "spaces-deletion-endpoint-member"
_SIGNING_KEY = "spaces-deletion-endpoint-secret-key-at-least-32-bytes"


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
                    "tenant": "spaces-deletion-endpoint-test",
                    "subject": {
                        "id": _USER_ID,
                        "username": "spaces-deletion-endpoint-user@example.com",
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


def _seed_empty_team_space(world) -> None:
    """An empty Team Space owned by the acting user — Space id 1."""
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="Deletion Endpoint Team", creator_id=_USER_ID
    )


def _seed_team_with_member(world) -> None:
    """A Team Space with one member but no Bot/Skill resources."""
    _seed_empty_team_space(world)
    world.get(SpaceMemberServiceProtocol).add_member(
        space_id=1,
        actor_id=_USER_ID,
        user_id=_MEMBER_ID,
        role=SpaceRole.MEMBER,
    )


def _seed_space_for_wrong_user(world) -> None:
    """A Team Space the verified caller does not administer."""
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="Deletion Endpoint Other Team", creator_id="other-admin-1"
    )


def _mismatched_user(path_params: dict) -> CaseInput:
    """The uniform error seam: user_id names someone other than the caller."""
    return CaseInput(
        path_params=path_params,
        query_params={"user_id": "someone-else"},
        headers=_principal_headers(),
    )


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}",
    scenario="happy",
    seed=_seed_empty_team_space,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"space_id": 1, "deleted": True}},
    ),
)
def delete_team_space_happy():
    """The current admin deletes an empty Team Space."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}",
    scenario="members_only",
    seed=_seed_team_with_member,
    input=CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": _USER_ID},
        headers=_principal_headers(),
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"space_id": 1, "deleted": True}},
    ),
)
def delete_team_space_members_only():
    """A Team Space with members but no resources is deletable."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/spaces/{space_id}",
    scenario="wrong_user",
    seed=_seed_space_for_wrong_user,
    input=_mismatched_user({"space_id": 1}),
    expect=ExpectError(status=403),
)
def delete_team_space_wrong_user():
    """A mismatched explicit actor is refused."""
