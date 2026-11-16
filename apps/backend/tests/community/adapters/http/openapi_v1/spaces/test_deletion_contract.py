"""Contract tests for the public team-space deletion route.

Covers the authorization and ownership invariants of
``DELETE /openapi/v1/bots/spaces/{space_id}``: only a current effective
team-space ADMIN may delete, historical ``ADMINISTRATOR`` membership rows
deletion-authorize like ADMIN, personal spaces are refused, and ownership
creation cannot bind a Skill to a soft-deleted Space.
"""

from __future__ import annotations

import time

import jwt
import pytest

from agentclaw.community.api.space_service import (
    SpaceDeletionServiceProtocol,
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.errors import SpaceNotFoundError
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
    SpaceSkillRepository,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401

_SIGNING_KEY = "space-delete-contract-signing-key-at-least-32-bytes"
_OWNER = "owner-1"
_MEMBER = "member-1"
_OUTSIDER = "outsider-1"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(user_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": user_id, "username": f"{user_id}@example.com"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


@pytest.fixture
def app(app_with_testing_modules):
    """The assembled app with a verifier key installed and public error handlers."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    mount_public_error_handlers(app_with_testing_modules)
    return app_with_testing_modules


def _request(app, method: str, path: str, user_id: str):
    client = user_scoped_client(app, user_id)
    return client.request(
        method, path, headers={"X-Avernet-Principal": _principal(user_id)}
    )


def _seed_team(world, *, name: str, creator: str = _OWNER) -> int:
    record = world.get(SpaceServiceProtocol).create_team(name=name, creator_id=creator)
    return record.id


def _seed_personal(world, *, user_id: str = _OWNER) -> int:
    record, _created = world.get(SpaceServiceProtocol).initialize_personal(
        user_id=user_id
    )
    return record.id


def _seed_member(world, *, space_id: int, user_id: str, actor: str = _OWNER) -> None:
    world.get(SpaceMemberServiceProtocol).add_member(
        space_id=space_id, actor_id=actor, user_id=user_id, role=SpaceRole.MEMBER
    )


def _seed_legacy_administrator_row(world, *, space_id: int, user_id: str) -> None:
    """Insert a historical ADMINISTRATOR membership row directly.

    The member service normalizes roles on write, so the legacy value can only
    exist as a historical row; create it the same way those rows were written.
    """
    env = get_current_env()
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            SpaceMemberModel(
                space_id=space_id,
                user_id=user_id,
                user_name=user_id,
                role="ADMINISTRATOR",
                status="ACTIVE",
                env=env,
                created_by=_OWNER,
            )
        )


def test_member_and_outsider_delete_refused(app, world):  # noqa: F811
    space_id = _seed_team(world, name="Deletion contract: guards")
    _seed_member(world, space_id=space_id, user_id=_MEMBER)

    member_status = _request(
        app, "DELETE", f"/openapi/v1/bots/spaces/{space_id}", _MEMBER
    ).status_code
    outsider_status = _request(
        app, "DELETE", f"/openapi/v1/bots/spaces/{space_id}", _OUTSIDER
    ).status_code
    members_status = _request(
        app, "GET", f"/openapi/v1/bots/spaces/{space_id}/members", _OWNER
    ).status_code

    assert member_status == 403
    assert outsider_status == 403
    assert members_status == 200


def test_admin_deletes_empty_team_space(app, world):  # noqa: F811
    space_id = _seed_team(world, name="Deletion contract: happy path")
    _seed_member(world, space_id=space_id, user_id=_MEMBER)

    resp = _request(app, "DELETE", f"/openapi/v1/bots/spaces/{space_id}", _OWNER)

    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True
    assert _request(
        app, "GET", f"/openapi/v1/bots/spaces/{space_id}/members", _OWNER
    ).status_code == 404


def test_legacy_administrator_row_can_delete(app, world):  # noqa: F811
    """Historical ADMINISTRATOR rows authorize deletion like canonical ADMIN.

    Regression test: the deletion check used to compare the raw persisted role
    column against the canonical value, refusing rows that every other surface
    exposes as administrators.
    """
    space_id = _seed_team(world, name="Deletion contract: legacy alias")
    _seed_legacy_administrator_row(world, space_id=space_id, user_id="legacy-admin-1")

    resp = _request(app, "DELETE", f"/openapi/v1/bots/spaces/{space_id}", "legacy-admin-1")

    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True


def test_personal_space_delete_refused(app, world):  # noqa: F811
    space_id = _seed_personal(world, user_id=_OWNER)

    resp = _request(app, "DELETE", f"/openapi/v1/bots/spaces/{space_id}", _OWNER)

    assert resp.status_code == 409
    assert resp.json()["code"] == 409317


def _seed_bot(world, *, bot_id: str, owner: str, space_id: int | None) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"bot-{bot_id}",
            "owner_id": owner,
            "owner_name": owner,
            "creator_id": owner,
            "entity_id": owner,
            "entity_type": "staff",
            "active_engine": "moltis",
            "bot_type": "service",
            "status": "ACTIVE",
            "space_id": space_id,
        }
    )


def test_bot_space_assignment_requires_active_space(app, world):  # noqa: F811
    """Bot space assignment refuses a soft-deleted target Space.

    Same serialization contract as skill ownership: ``update_space_by_owner``
    locks the target Space row and revalidates it inside the write
    transaction, so a Space deleted by a concurrent transaction is observed
    instead of being assigned to a Bot after the deletion committed.
    """
    space_id = _seed_team(world, name="Deletion contract: bot assignment guard")
    _seed_bot(world, bot_id="bot-assign-1", owner=_OWNER, space_id=None)
    world.get(SpaceDeletionServiceProtocol).delete_team_space(
        space_id=space_id, actor_id=_OWNER
    )
    repository = world.get(BotRepository)

    with pytest.raises(SpaceNotFoundError):
        repository.update_space_by_owner(
            bot_id="bot-assign-1", owner_id=_OWNER, space_id=space_id
        )


def test_bot_creation_requires_active_space(app, world):  # noqa: F811
    """Bot creation refuses a soft-deleted target Space.

    Same serialization contract as assignment and skill ownership: the
    creation transaction locks the owning Space row and revalidates it, so a
    Space deleted by a concurrent transaction is observed instead of
    receiving a live Bot after the deletion committed.
    """
    space_id = _seed_team(world, name="Deletion contract: bot creation guard")
    world.get(SpaceDeletionServiceProtocol).delete_team_space(
        space_id=space_id, actor_id=_OWNER
    )
    repository = world.get(BotRepository)
    env = get_current_env()

    with pytest.raises(SpaceNotFoundError):
        repository.insert_with_initial_skill_layout(
            {
                "bot_id": "bot-create-1",
                "bot_name": "bot-create-1",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "creator_id": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "active_engine": "moltis",
                "bot_type": "service",
                "status": "ACTIVE",
                "space_id": space_id,
                "env": env,
            },
            layout=None,
        )


def test_skill_ownership_requires_active_space(app, world):  # noqa: F811
    """Skill ownership creation refuses a soft-deleted Space.

    Guards the serialization contract with delete_team_space: the ownership
    read takes the same row lock, so a Space deleted by a concurrent
    transaction is observed here instead of being bound to a new Skill.
    """
    space_id = _seed_team(world, name="Deletion contract: ownership guard")
    world.get(SpaceDeletionServiceProtocol).delete_team_space(
        space_id=space_id, actor_id=_OWNER
    )
    repository = world.get(SpaceSkillRepository)
    env = get_current_env()

    with pytest.raises(ValueError, match="requires an active Space"):
        repository.create_space_skill(
            skill_data={
                "env": env,
                "name": "deleted-space-skill",
                "status": "DEVELOPING",
                "version": 1,
                "creation_request_id": "req-delete-contract-1",
                "creation_request_hash": "hash-delete-contract-1",
            },
            ownership_data={
                "env": env,
                "space_id": space_id,
            },
            owner_grant_data={
                "env": env,
                "user_id": _OWNER,
            },
        )
