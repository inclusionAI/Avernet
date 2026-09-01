"""Endpoint coverage for authenticated Caller identity configuration.

The context read uses the local database and lock.  The PATCH case pins its
HTTP route and response mapping; the core service unit tests cover its real
transaction, lock, and Agent Principal synchronization branches.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
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
_PRINCIPAL_KEY = "caller-identity-openapi-signing-key-32b"


class _Secret:
    secret_user = "test"
    secret_value = _PRINCIPAL_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
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
                    "subject": {
                        "id": _OWNER_ID,
                        "username": "caller-identity@example.test",
                    },
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )


_OPENAPI_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _enable_openapi_principal() -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_owner(world) -> None:
    make_staff_user(world, user_id=_OWNER_ID)


def _seed_service_bot(world, *, acquire_lock: bool) -> None:
    _seed_owner(world)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        bot_type="service",
        status="ACTIVE",
    )
    if acquire_lock:
        world.get(CollaboratorLockService).acquire_lock(
            _BOT_ID,
            _OWNER_ID,
            _OWNER_ID,
        )


def _seed_editable_service_bot(world) -> None:
    _seed_service_bot(world, acquire_lock=True)


def _seed_openapi_editable_service_bot(world) -> None:
    _enable_openapi_principal()
    _seed_editable_service_bot(world)


def _seed_mutable_caller_identity(world, *, acquire_lock: bool = True) -> None:
    _seed_service_bot(world, acquire_lock=acquire_lock)
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


def _seed_unlocked_mutable_caller_identity(world) -> None:
    _seed_mutable_caller_identity(world, acquire_lock=False)


def _seed_openapi_unlocked_mutable_caller_identity(world) -> None:
    _enable_openapi_principal()
    _seed_unlocked_mutable_caller_identity(world)


def _seed_openapi_owner(world) -> None:
    _enable_openapi_principal()
    _seed_owner(world)


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
            "cli_call_types": {},
            "editable": True,
        },
    ),
)
def get_caller_context_happy():
    """The owner holding the draft lock reads its Caller context."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/caller-context",
    scenario="gateway_ctoken_compatibility",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={
            "stage": "draft",
            "entity_id": _OWNER_ID,
            "ctoken": "opaque-gateway-compatibility-value",
        },
        headers={"x-user-id": _OWNER_ID},
    ),
    seed=_seed_editable_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "capability": "caller_identity.v1",
            "stage": "draft",
            "editable": True,
        },
    ),
)
def get_caller_context_accepts_gateway_ctoken():
    """Gateway compatibility query values do not change Caller reads."""


@endpoint_test(
    method="GET",
    path="/api/bots/{bot_id}/caller-context",
    scenario="context_rejects_unknown_query",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={
            "stage": "draft",
            "unexpected": "rejected",
        },
        headers={"x-user-id": _OWNER_ID},
    ),
    seed=_seed_editable_service_bot,
    expect=ExpectError(status=422),
)
def get_caller_context_rejects_unknown_query_parameter():
    """Only the documented ctoken compatibility parameter is tolerated."""


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
    scenario="unlocked_owner_without_epoch",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _SERVER_CODE},
        query_params={"entity_id": _OWNER_ID},
        headers={"x-user-id": _OWNER_ID},
        json_body={"call_type": "caller"},
    ),
    seed=_seed_unlocked_mutable_caller_identity,
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
def update_mcp_call_type_without_lock_epoch_for_unlocked_owner():
    """A single owner may update Caller identity before a lock exists."""


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


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/caller-context",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"user_id": _OWNER_ID, "stage": "draft"},
        headers=_OPENAPI_HEADERS,
    ),
    seed=_seed_openapi_editable_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "capability": "caller_identity.v1",
                "stage": "draft",
                "bot_call_type": "owner",
                "cli_call_types": {},
                "editable": True,
            },
        },
    ),
)
def get_openapi_caller_context_happy():
    """The public endpoint wraps an authorized draft context in an Envelope."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/caller-context",
    scenario="error",
    input=CaseInput(
        path_params={"bot_id": "missing_caller_identity_bot"},
        query_params={"user_id": _OWNER_ID, "stage": "draft"},
        headers=_OPENAPI_HEADERS,
    ),
    seed=_seed_openapi_owner,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found", "data": None},
    ),
)
def get_openapi_caller_context_not_found():
    """An absent addressed Bot is returned as the public masked 404."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type",
    scenario="happy",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _SERVER_CODE},
        query_params={"user_id": _OWNER_ID},
        headers=_OPENAPI_HEADERS,
        json_body={"call_type": "caller"},
    ),
    seed=_seed_openapi_unlocked_mutable_caller_identity,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "server_code": _SERVER_CODE,
                "call_type": "caller",
                "bot_call_type": "caller",
            },
        },
    ),
)
def update_openapi_mcp_call_type_happy():
    """A sole owner updates an active MCP without a client-supplied lock epoch."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type",
    scenario="error",
    input=CaseInput(
        path_params={
            "bot_id": "missing_caller_identity_bot",
            "server_code": _SERVER_CODE,
        },
        query_params={"user_id": _OWNER_ID},
        headers=_OPENAPI_HEADERS,
        json_body={"call_type": "caller"},
    ),
    seed=_seed_openapi_owner,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found", "data": None},
    ),
)
def update_openapi_mcp_call_type_not_found():
    """The owner-only mutation masks an absent Bot behind the standard 404."""
