"""End-to-end coverage for Team Space deletion.

Each case uses the real OpenAPI route and a fresh SQLite database. Failed
resource and authorization cases also inspect live Space state after the
response, proving that the repository rolled back without deleting the Space.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.models.space_skill import SkillSpaceBinding
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.spaces.models import SpaceListScope
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT as RESOURCE_TENANT
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_ADMIN_ID = "space-deletion-admin"
_MEMBER_ID = "space-deletion-member"
_SIGNING_KEY = "space-deletion-secret-key-at-least-32-bytes"
_TENANT = "space-deletion-test"
_PATH = "/openapi/v1/bots/spaces/{space_id}"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers(user_id: str = _ADMIN_ID) -> dict[str, str]:
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
                    "tenant": _TENANT,
                    "subject": {
                        "id": user_id,
                        "username": f"{user_id}@example.com",
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


def _seed_team_by_current_admin(world) -> None:
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name=" deletable team", creator_id=_ADMIN_ID
    )


def _seed_team_by_other_admin(world) -> None:
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="another admin team", creator_id="another-space-admin"
    )


def _seed_personal(world) -> None:
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).initialize_personal(user_id=_ADMIN_ID)


def _seed_team_with_member(world) -> None:
    _seed_team_by_other_admin(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            SpaceMemberModel(
                space_id=1,
                user_id=_MEMBER_ID,
                role="MEMBER",
                status="ACTIVE",
                env=get_current_env(),
                created_by="another-space-admin",
            )
        )


def _seed_demoted_creator(world) -> None:
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="former creator team", creator_id=_ADMIN_ID
    )
    with world.get(DatabasePlugin).orm_session() as session:
        member = session.query(SpaceMemberModel).filter_by(space_id=1).one()
        member.role = "MEMBER"


def _seed_legacy_administrator(world) -> None:
    _enable_public_auth(world)
    world.get(SpaceServiceProtocol).create_team(
        name="legacy administrator team", creator_id=_ADMIN_ID
    )
    with world.get(DatabasePlugin).orm_session() as session:
        member = session.query(SpaceMemberModel).filter_by(space_id=1).one()
        member.role = "ADMINISTRATOR"


def _seed_live_bot(world) -> None:
    _seed_team_by_current_admin(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            BotModel(
                bot_id="space-deletion-bot",
                entity_id="space-deletion-entity",
                entity_type="APP",
                creator_id=_ADMIN_ID,
                owner_id=_ADMIN_ID,
                status="ONLINE",
                env=get_current_env(),
                space_id=1,
                is_delete=0,
                avernet_tenant=RESOURCE_TENANT,
            )
        )


def _seed_space_skill(world) -> None:
    _seed_team_by_current_admin(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            SkillSpaceBinding(
                skill_id=101,
                space_id=1,
                created_by=_ADMIN_ID,
                env=get_current_env(),
                avernet_tenant=RESOURCE_TENANT,
            )
        )


def _input(user_id: str = _ADMIN_ID, query_user_id: str | None = None) -> CaseInput:
    return CaseInput(
        path_params={"space_id": 1},
        query_params={"user_id": query_user_id or user_id},
        headers=_principal_headers(user_id),
    )


def _assert_space_remains_live(_response, world) -> None:
    total, items = world.get(SpaceServiceProtocol).list_spaces(
        user_id=_ADMIN_ID,
        keyword=None,
        space_type=None,
        page_no=1,
        page_size=20,
        scope=SpaceListScope.ALL,
    )
    assert total >= 1
    assert any(item.space.id == 1 for item in items)


def _assert_space_is_hidden(_response, world) -> None:
    total, items = world.get(SpaceServiceProtocol).list_spaces(
        user_id=_ADMIN_ID,
        keyword=None,
        space_type=None,
        page_no=1,
        page_size=20,
        scope=SpaceListScope.ACCESSIBLE,
    )
    assert total == 0
    assert not any(item.space.id == 1 for item in items)


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_happy",
    seed=_seed_team_by_current_admin,
    input=_input(),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"space_id": 1, "deleted": True}},
    ),
    extra_assertions=(_assert_space_is_hidden,),
)
def delete_team_space_happy():
    """The current active admin can delete an empty TEAM Space."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_mismatched_user",
    seed=_seed_team_by_current_admin,
    input=_input(query_user_id="someone-else"),
    expect=ExpectError(status=403),
)
def delete_team_space_mismatched_user():
    """The signed actor cannot name another user as the deleter."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_ordinary_member_forbidden",
    seed=_seed_team_with_member,
    input=_input(user_id=_MEMBER_ID),
    expect=ExpectError(status=403),
    extra_assertions=(_assert_space_remains_live,),
)
def delete_team_space_ordinary_member_forbidden():
    """An active ordinary member cannot delete the Team Space."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_nonmember_forbidden",
    seed=_seed_team_by_other_admin,
    input=_input(),
    expect=ExpectError(status=403),
    extra_assertions=(_assert_space_remains_live,),
)
def delete_team_space_nonmember_forbidden():
    """A nonmember cannot delete another team's Space."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_demoted_creator_forbidden",
    seed=_seed_demoted_creator,
    input=_input(),
    expect=ExpectError(status=403),
    extra_assertions=(_assert_space_remains_live,),
)
def delete_team_space_demoted_creator_forbidden():
    """The historical creator exception does not survive demotion."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_legacy_administrator_allowed",
    seed=_seed_legacy_administrator,
    input=_input(),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"space_id": 1, "deleted": True}},
    ),
    extra_assertions=(_assert_space_is_hidden,),
)
def delete_team_space_legacy_administrator_allowed():
    """A historical active ADMINISTRATOR row remains an administrator."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_personal_space_forbidden",
    seed=_seed_personal,
    input=_input(),
    expect=ExpectError(status=409),
    extra_assertions=(_assert_space_remains_live,),
)
def delete_personal_space_forbidden():
    """Personal Spaces are immutable and are never deleted by this endpoint."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_with_live_bot_conflict",
    seed=_seed_live_bot,
    input=_input(),
    expect=ExpectError(status=409),
    extra_assertions=(_assert_space_remains_live,),
)
def delete_team_space_with_live_bot_conflict():
    """A live Bot assigned to the Space blocks deletion without state change."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="delete_team_space_with_space_skill_conflict",
    seed=_seed_space_skill,
    input=_input(),
    expect=ExpectError(status=409),
    extra_assertions=(_assert_space_remains_live,),
)
def delete_team_space_with_space_skill_conflict():
    """A Space Skill binding blocks deletion without hiding the Space."""
