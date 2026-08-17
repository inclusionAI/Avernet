"""Endpoint-framework coverage for the completed MCP category.

Covers the eight operations added in the MCP completion: the two account-level
config-lifecycle routes and the six bot-scoped activation routes. The framework
owns invocation, so a case's declared ``(method, path)`` is necessarily the
endpoint exercised.

The MCP Center and the device-sync service are the two things a test cannot
have: one is an external marketplace, the other pushes to a container. Both are
stubbed here; everything below them — the repositories, the default skill set,
the exclusion rows — is real, so these cases exercise the actual state machine
rather than a mock of it.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_mcp_state_service import BotMcpStateServiceProtocol
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.mcp.services.bot_mcp_state_service import (
    BotMcpStateService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "mcp-lifecycle-owner"
_BOT_ID = "mcp-lifecycle-bot"
_TENANT = "mcp-lifecycle-tenant"
_KEY = "mcp-lifecycle-framework-signing-key-32b"
_CODE = "mcp.example.weather"
_UNKNOWN = "mcp.example.nope"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Center:
    """Stub MCP Center: knows exactly one visible server."""

    def get_mcp_detail(self, server_code: str):
        if server_code != _CODE:
            return None
        return {
            "serverCode": _CODE,
            "name": "Weather",
            "description": "Forecasts",
            "networkTypes": ["INTERNET"],
            "transportProtocol": "SSE",
        }


class _Sync:
    """Stub device sync. ``ok=False`` makes every reconcile fail."""

    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    async def refresh_mcp_scope(self, **_kwargs):
        return {"success": self._ok, "error": None if self._ok else "device down"}

    async def sync_mcp_detail_to_all_bots(self, **_kwargs):
        return {
            "success": self._ok,
            "sync_results": [],
            "error": None if self._ok else "device down",
        }

    async def remove_mcp_detail(self, **_kwargs):
        return {"success": self._ok, "error": None}


class _Market:
    def get_mcp_detail(self, server_code: str):
        return _Center().get_mcp_detail(server_code)


class _Config:
    """Stub unified-config store, seeded with one stored config."""

    def __init__(self, stored: bool = True) -> None:
        self._stored = stored

    def list_user_unified_configs(self, _user_id):
        if not self._stored:
            return []
        return [
            {
                "server_code": _CODE,
                "api_key": "sk-abcdefghijkl",
                "headers": {},
                "endpoint_env": "PROD",
                "transport_protocol": "SSE",
            }
        ]

    def delete_user_unified_config(self, *, user_id, server_code):
        if not self._stored:
            return None
        return {
            "api_key": "sk-abcdefghijkl",
            "headers": {},
            "endpoint_env": "PROD",
            "transport_protocol": "SSE",
        }

    def rollback_unified_config(self, **_kwargs):
        return None


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
                    "subject": {"id": _OWNER, "username": "mcp@example.test"},
                },
                {
                    "type": "app",
                    "tenant": _TENANT,
                    "app": {
                        "app_id": 1,
                        "app_name": "MCP Lifecycle Test App",
                        "owners": "mcp-org",
                        "tenant": _TENANT,
                    },
                },
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _bind_bot_state(world, *, sync_ok: bool = True) -> None:
    world.injector.binder.bind(
        BotMcpStateServiceProtocol,
        to=BotMcpStateService(
            skill_set_repo=world.get(SkillSetRepository),
            bot_repo=world.get(BotRepository),
            mcp_center=_Center(),
            sync_service=_Sync(sync_ok),
        ),
        scope=None,
    )


def _seed_bot(world, *, on_bot: bool = False, active: bool = False) -> None:
    """Insert the bot and its default skill set; optionally put the server on it."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "MCP Lifecycle Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        skill_set = world.get(SkillSetRepository).create(
            {
                "name": "Default",
                "description": "Default Skill Set",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "is_default": True,
                "is_builtin": False,
                "is_active": False,
                "engine_type": "openclaw",
            }
        )
        if on_bot:
            world.get(SkillSetRepository).add_mcp_to_set(
                str(skill_set["id"]), _CODE, "Weather", user_id=_OWNER
            )
            if not active:
                world.get(SkillSetRepository).add_default_mcp_exclusion(
                    user_id=_OWNER,
                    bot_id=_BOT_ID,
                    skill_set_id=int(skill_set["id"]),
                    server_code=_CODE,
                )


def _seed_empty_bot(world) -> None:
    _seed_bot(world)
    _bind_bot_state(world)


def _seed_server_inactive(world) -> None:
    _seed_bot(world, on_bot=True, active=False)
    _bind_bot_state(world)


def _seed_server_active(world) -> None:
    _seed_bot(world, on_bot=True, active=True)
    _bind_bot_state(world)


def _seed_sync_failure(world) -> None:
    _seed_bot(world, on_bot=True, active=False)
    _bind_bot_state(world, sync_ok=False)


def _bind_account_level(world, *, stored: bool = True, sync_ok: bool = True) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    world.injector.binder.bind(
        MCPConfigServiceProtocol, to=_Config(stored), scope=None
    )
    world.injector.binder.bind(MCPMarketServiceProtocol, to=_Market(), scope=None)
    world.injector.binder.bind(MCPSyncServiceProtocol, to=_Sync(sync_ok), scope=None)


def _seed_configs(world) -> None:
    _bind_account_level(world)


def _seed_no_configs(world) -> None:
    _bind_account_level(world, stored=False)


def _seed_delete_sync_failure(world) -> None:
    _bind_account_level(world, sync_ok=False)


_QUERY = {"user_id": _OWNER}
_BOT_PATH = {"bot_id": _BOT_ID}


# ── account level: list configs ─────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/mcp/configs",
    scenario="lists_the_callers_configs_masked",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS),
    seed=_seed_configs,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"total": 1}},
    ),
)
def list_configs_ok():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/mcp/configs",
    scenario="refuses_a_user_id_naming_someone_else",
    input=CaseInput(
        query_params={"user_id": "someone-else"}, headers=_HEADERS
    ),
    seed=_seed_no_configs,
    expect=ExpectError(status=403, json_contains={"message": "Forbidden"}),
)
def list_configs_forbidden():
    pass


# ── account level: delete config ────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/mcp/servers/{server_code}/config",
    scenario="deletes_the_stored_config",
    input=CaseInput(
        path_params={"server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_configs,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def delete_config_ok():
    pass


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/mcp/servers/{server_code}/config",
    scenario="unknown_server_is_not_found",
    input=CaseInput(
        path_params={"server_code": _UNKNOWN},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_configs,
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def delete_config_unknown_server():
    pass


# ── bot scoped: list ────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/mcp",
    scenario="lists_the_bots_servers",
    input=CaseInput(path_params=_BOT_PATH, query_params=_QUERY, headers=_HEADERS),
    seed=_seed_server_inactive,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def list_bot_servers_ok():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/mcp",
    scenario="a_bot_the_caller_does_not_own_is_not_found",
    input=CaseInput(
        path_params={"bot_id": "someone-elses-bot"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_empty_bot,
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def list_bot_servers_unowned_bot():
    pass


# ── bot scoped: get one ─────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}",
    scenario="reads_one_servers_state",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_server_inactive,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"server_code": _CODE, "active": False}},
    ),
)
def get_bot_server_ok():
    pass


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}",
    scenario="a_server_not_on_the_bot_is_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_empty_bot,
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def get_bot_server_not_on_bot():
    pass


# ── bot scoped: add ─────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/mcp",
    scenario="adds_the_server_deactivated",
    input=CaseInput(
        path_params=_BOT_PATH,
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"server_code": _CODE},
    ),
    seed=_seed_empty_bot,
    expect=ExpectSuccess(
        status=201,
        json_contains={"code": 201000, "data": {"changed": True}},
    ),
)
def add_bot_server_ok():
    pass


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/mcp",
    scenario="unknown_server_is_not_found",
    input=CaseInput(
        path_params=_BOT_PATH,
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"server_code": _UNKNOWN},
    ),
    seed=_seed_empty_bot,
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def add_bot_server_unknown():
    pass


# ── bot scoped: activate ────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}/activate",
    scenario="activates_an_inactive_server",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_server_inactive,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"changed": True}},
    ),
)
def activate_ok():
    pass


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}/activate",
    scenario="a_runtime_that_cannot_reconcile_is_a_device_sync_failure",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_sync_failure,
    expect=ExpectError(status=502, json_contains={"message": "Device sync failed"}),
)
def activate_runtime_failure():
    pass


# ── bot scoped: deactivate ──────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}/deactivate",
    scenario="deactivates_an_active_server",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_server_active,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"changed": True}},
    ),
)
def deactivate_ok():
    pass


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}/deactivate",
    scenario="a_server_not_on_the_bot_is_not_found",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_empty_bot,
    expect=ExpectError(status=404, json_contains={"message": "Not found"}),
)
def deactivate_not_on_bot():
    pass


# ── bot scoped: remove ──────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}",
    scenario="removes_the_server_from_the_bot",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_server_inactive,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"removed": True}},
    ),
)
def remove_bot_server_ok():
    pass


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/mcp/{server_code}",
    scenario="refuses_a_user_id_naming_someone_else",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "server_code": _CODE},
        query_params={"user_id": "someone-else"},
        headers=_HEADERS,
    ),
    seed=_seed_server_inactive,
    expect=ExpectError(status=403, json_contains={"message": "Forbidden"}),
)
def remove_bot_server_forbidden():
    pass
