"""Endpoint coverage for authenticated Caller identity configuration.

The context read uses the local database and lock.  The PATCH case pins its
HTTP route and response mapping; the core service unit tests cover its real
transaction, lock, and Agent Principal synchronization branches.
"""

from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillSetRepository,
)
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER_ID = "caller_identity_owner"
_BOT_ID = "caller_identity_bot"
_SERVER_CODE = "mcp.caller.identity"


def _seed_owner(world) -> None:
    make_staff_user(world, user_id=_OWNER_ID)


def _seed_editable_service_bot(world) -> None:
    _seed_owner(world)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        bot_type="service",
        status="ACTIVE",
    )
    world.get(CollaboratorLockService).acquire_lock(
        _BOT_ID,
        _OWNER_ID,
        _OWNER_ID,
    )


def _seed_mutable_caller_identity(world) -> None:
    _seed_editable_service_bot(world)
    bot = world.get(BotRepository).get_by_id(_BOT_ID)
    engine_type = str(bot["active_engine"])
    skill_set = world.get(SkillSetRepository).create(
        {
            "name": "Caller identity test set",
            "description": "",
            "user_id": _OWNER_ID,
            "bolt_id": _BOT_ID,
            "is_default": False,
            "is_builtin": False,
            "is_active": 1,
            "engine_type": engine_type,
        }
    )
    world.get(SkillSetRepository).add_mcp_to_set(
        skill_set["id"],
        _SERVER_CODE,
        "Caller identity MCP",
        description="",
        icon="",
        user_id=_OWNER_ID,
        env=get_current_env(),
    )


def _seed_ambiguous_default_bots(world) -> None:
    _seed_owner(world)
    make_bot(
        world,
        bot_id="default",
        owner_id=_OWNER_ID,
        bot_type="service",
        status="ACTIVE",
    )
    make_bot(
        world,
        bot_id="default",
        owner_id="other_default_owner",
        bot_type="service",
        status="ACTIVE",
    )


def _assert_mcp_call_type_updated(response, world) -> None:
    body = response.json()
    assert body["call_type"] == "caller", body
    assert body["bot_call_type"] == "caller", body


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/caller-context",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"stage": "draft", "entity_id": _OWNER_ID},
        headers={"x-user-id": _OWNER_ID},
    ),
    seed=_seed_editable_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "capability": "caller_identity.v1",
            "stage": "draft",
            "bot_call_type": "owner",
            "editable": True,
        },
    ),
)
def get_caller_context_happy():
    """The owner holding the draft lock reads its Caller context."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/caller-context",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": "missing_caller_identity_bot"},
        query_params={"stage": "draft"},
        headers={"x-user-id": _OWNER_ID},
    ),
    seed=_seed_owner,
    expect=ExpectError(
        status=404,
        json_contains={"detail": "BOT_NOT_FOUND"},
    ),
)
def get_caller_context_not_found():
    """A caller-context request for an unknown bot is rejected."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/caller-context",
    scenario="ambiguous_default_without_entity",
    input=CaseInput(
        path_params={"bot_id": "default"},
        query_params={"stage": "draft"},
        headers={"x-user-id": _OWNER_ID},
    ),
    seed=_seed_ambiguous_default_bots,
    expect=ExpectError(
        status=409,
        json_contains={"detail": "CALLER_IDENTITY_AMBIGUOUS"},
    ),
)
def get_caller_context_rejects_ambiguous_default_without_entity():
    """Caller reads must not select an arbitrary default Bot."""


@endpoint_test(
    method="PATCH",
    path="/api/bots/{bot_id}/mcps/{server_code}/call-type",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _SERVER_CODE},
        query_params={
            "ctoken": "opaque-gateway-compatibility-value",
            "entity_id": _OWNER_ID,
        },
        headers={"x-user-id": _OWNER_ID},
        json_body={"call_type": "caller", "lock_epoch": 1},
    ),
    seed=_seed_mutable_caller_identity,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "server_code": _SERVER_CODE,
            "call_type": "caller",
            "bot_call_type": "caller",
        },
    ),
    extra_assertions=(_assert_mcp_call_type_updated,),
)
def update_mcp_call_type_happy():
    """The HTTP route maps an accepted Caller update to its response payload."""


@endpoint_test(
    method="PATCH",
    path="/api/bots/{bot_id}/mcps/{server_code}/call-type",
    scenario="invalid_query",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _SERVER_CODE},
        query_params={"unexpected": "rejected"},
        headers={"x-user-id": _OWNER_ID},
        json_body={"call_type": "caller", "lock_epoch": 1},
    ),
    seed=_seed_mutable_caller_identity,
    expect=ExpectError(status=422),
)
def update_mcp_call_type_rejects_unknown_query_parameter():
    """Only documented compatibility query parameters are accepted."""


@endpoint_test(
    method="PATCH",
    path="/api/bots/{bot_id}/mcps/{server_code}/call-type",
    scenario="error",
    input=CaseInput(
        path_params={
            "bot_id": "missing_caller_identity_bot",
            "server_code": _SERVER_CODE,
        },
        headers={"x-user-id": _OWNER_ID},
        json_body={"call_type": "caller", "lock_epoch": 1},
    ),
    seed=_seed_owner,
    expect=ExpectError(
        status=403,
        json_contains={"detail": "CALLER_IDENTITY_FORBIDDEN"},
    ),
)
def update_mcp_call_type_forbidden():
    """Only an owner of an existing service bot can change MCP call type."""
