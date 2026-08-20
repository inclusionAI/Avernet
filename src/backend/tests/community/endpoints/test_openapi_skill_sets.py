"""Endpoint-framework coverage for every canonical SkillSet operation.

The cases use the production control-plane service and SQLite repositories.  A
recording runtime is the only substituted boundary, so the tests exercise the
same ACL, desired-state UoW, and router seam as a real request.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.skill_set_control_plane import (
    SkillSetControlPlaneServiceProtocol,
)
from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.skill_set_control_plane import (
    SkillSetControlPlaneRepositoryProtocol,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.services.bot_capability_mutation_guard import (
    BotCapabilityMutationGuard,
)
from agentclaw.community.core.skill_center.services.skill_set_control_plane import (
    SkillSetControlPlaneService,
)
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.mcp_auth import MCPAuthPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
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


_OWNER = "skill-set-owner"
_BOT_ID = "skill-set-bot"
_TENANT = "skill-set-tenant"
_KEY = "skill-set-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def reconcile(self, *, bot_id: str, owner_id: str) -> None:
        self.calls.append((bot_id, owner_id))


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
                    "subject": {"id": _OWNER, "username": "skill-set@example.test"},
                },
                {
                    "type": "app",
                    "tenant": _TENANT,
                    "app": {
                        "app_id": 1,
                        "app_name": "SkillSet Test App",
                        "owners": "test",
                        "tenant": _TENANT,
                    },
                },
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _seed(world, *, member: bool = False) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "SkillSet endpoint Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        repository = world.get(SkillSetControlPlaneRepositoryProtocol)
        skill_set = repository.create_set(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            name="Endpoint set",
            description=None,
            idempotency_key="endpoint-set",
            engine_type="openclaw",
        )
        skill = world.get(SkillRepository).create(
            {
                "name": "endpoint-skill",
                "description": "Endpoint SkillSet coverage",
                "git_path": "git://endpoint-skill",
                "category": "general",
                "tags": "[]",
                "is_public": True,
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "source_type": "git",
            }
        )
        if member:
            repository.add_skill(
                bot_id=_BOT_ID,
                owner_id=_OWNER,
                set_id=str(skill_set["id"]),
                skill_id=str(skill["id"]),
                engine_type="openclaw",
            )
    runtime = _Runtime()
    control_plane = SkillSetControlPlaneService(
        repository=world.get(SkillSetControlPlaneRepositoryProtocol),
        bot_repo=world.get(BotRepository),
        runtime=runtime,
        legacy_factory=world.get(SkillSetServiceFactory),
        passport=world.get(PassportPlugin),
        authorization=world.get(BotCapabilityAuthorizationHookProtocol),
        mutation_guard=world.get(BotCapabilityMutationGuard),
        edit_guard=world.get(SkillsPoolEditGuard),
        audit_log_repo=world.get(BotCollabLogRepositoryProtocol),
        mcp_center=world.get(MCPCenterPlugin),
        mcp_auth=world.get(MCPAuthPlugin),
    )
    world.injector.binder.bind(
        SkillSetControlPlaneServiceProtocol, to=control_plane, scope=None
    )


def _seed_member(world) -> None:
    _seed(world, member=True)


def _seed_active(world) -> None:
    _seed_member(world)
    with avernet_tenant_scope(_TENANT):
        world.get(SkillSetControlPlaneRepositoryProtocol).set_active(
            bot_id=_BOT_ID, set_id="1", active=True, engine_type="openclaw"
        )


def _assert_reconciled(_response, world) -> None:
    assert world.get(SkillSetControlPlaneServiceProtocol)._runtime.calls == [
        (_BOT_ID, _OWNER)
    ]


def _case(
    method,
    path,
    scenario,
    expect,
    *,
    seed=_seed,
    json_body=None,
    headers=_HEADERS,
    extra=(),
    path_params=None,
):
    return endpoint_test(
        method=method,
        path=path,
        scenario=scenario,
        expect=expect,
        input=CaseInput(
            path_params=path_params
            or {"bot_id": _BOT_ID, "set_id": "1", "skill_id": "1", "server_code": "mcp.test"},
            query_params={"user_id": _OWNER},
            headers=headers,
            json_body=json_body,
        ),
        seed=seed,
        extra_assertions=extra,
    )


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets",
    "lists_sets",
    ExpectSuccess(status=200, json_contains={"code": 200000, "data": [{"id": "1"}]}),
)
def list_sets_happy():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets",
    "missing_bot",
    ExpectError(status=404),
    seed=lambda world: init_principal_verifier_config(
        _Resolver(), "test-key", strict=False
    ),
)
def list_sets_error():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets",
    "creates_inactive_set",
    ExpectSuccess(status=201, json_contains={"data": {"is_active": False}}),
    json_body={"name": "Created"},
    headers={**_HEADERS, "Idempotency-Key": "create-set"},
)
def create_set_happy():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets",
    "rejects_duplicate_name",
    ExpectError(status=409),
    json_body={"name": "Endpoint set"},
    headers={**_HEADERS, "Idempotency-Key": "duplicate-set"},
)
def create_set_error():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/resources",
    "lists_resources",
    ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": [{"id": "1", "mcps": [], "clis": []}]},
    ),
)
def resources_happy():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/resources",
    "missing_bot",
    ExpectError(status=404),
    seed=lambda world: init_principal_verifier_config(
        _Resolver(), "test-key", strict=False
    ),
)
def resources_error():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    "gets_set",
    ExpectSuccess(
        status=200, json_contains={"data": {"id": "1", "name": "Endpoint set"}}
    ),
)
def get_set_happy():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    "missing_set",
    ExpectError(status=404),
    path_params={"bot_id": _BOT_ID, "set_id": "999"},
)
def get_set_error():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    "updates_metadata",
    ExpectSuccess(status=200, json_contains={"data": {"name": "Renamed"}}),
    json_body={"name": "Renamed"},
)
def update_set_happy():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    "missing_set",
    ExpectError(status=404),
    json_body={"name": "Renamed"},
    path_params={"bot_id": _BOT_ID, "set_id": "999"},
)
def update_set_error():
    pass


@_case(
    "DELETE",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    "deletes_inactive_set",
    ExpectSuccess(status=200, json_contains={"data": {"deleted": True}}),
)
def delete_set_happy():
    pass


@_case(
    "DELETE",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}",
    "rejects_active_set",
    ExpectError(status=409),
    seed=_seed_active,
)
def delete_set_error():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills",
    "lists_members",
    ExpectSuccess(status=200, json_contains={"data": [{"skill_id": "1"}]}),
    seed=_seed_member,
)
def list_members_happy():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills",
    "missing_set",
    ExpectError(status=404),
    path_params={"bot_id": _BOT_ID, "set_id": "999"},
)
def list_members_error():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "adds_member",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
)
def add_member_happy():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "missing_set",
    ExpectError(status=404),
    path_params={"bot_id": _BOT_ID, "set_id": "999", "skill_id": "1"},
)
def add_member_error():
    pass


@_case(
    "DELETE",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "removes_member",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
    seed=_seed_member,
)
def remove_member_happy():
    pass


@_case(
    "DELETE",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "missing_set",
    ExpectError(status=404),
    path_params={"bot_id": _BOT_ID, "set_id": "999", "skill_id": "1"},
)
def remove_member_error():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate",
    "activates_set",
    ExpectSuccess(status=200, json_contains={"data": {"is_active": True}}),
    seed=_seed_member,
    extra=(_assert_reconciled,),
)
def activate_happy():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate",
    "missing_set",
    ExpectError(status=404),
    path_params={"bot_id": _BOT_ID, "set_id": "999"},
)
def activate_error():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate",
    "deactivates_set",
    ExpectSuccess(status=200, json_contains={"data": {"is_active": False}}),
    seed=_seed_member,
    extra=(_assert_reconciled,),
)
def deactivate_happy():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate",
    "missing_set",
    ExpectError(status=404),
    path_params={"bot_id": _BOT_ID, "set_id": "999"},
)
def deactivate_error():
    pass


def _seed_mcp_member(world) -> None:
    _seed(world)
    with avernet_tenant_scope(_TENANT):
        world.get(SkillSetControlPlaneRepositoryProtocol).add_mcp(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            set_id="1",
            server_code="mcp.test",
            engine_type="openclaw",
        )


@_case("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps", "lists_mcps", ExpectSuccess(status=200, json_contains={"data": []}))
def list_mcps_happy():
    pass


@_case("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps", "missing_set", ExpectError(status=404), path_params={"bot_id": _BOT_ID, "set_id": "999", "server_code": "mcp.test"})
def list_mcps_error():
    pass


@_case("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}", "adds_mcp", ExpectSuccess(status=200, json_contains={"data": {"changed": True}}))
def add_mcp_happy():
    pass


@_case("PUT", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}", "missing_set", ExpectError(status=404), path_params={"bot_id": _BOT_ID, "set_id": "999", "server_code": "mcp.test"})
def add_mcp_error():
    pass


@_case("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}", "removes_mcp", ExpectSuccess(status=200, json_contains={"data": {"changed": True}}), seed=_seed_mcp_member)
def remove_mcp_happy():
    pass


@_case("DELETE", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}", "missing_set", ExpectError(status=404), path_params={"bot_id": _BOT_ID, "set_id": "999", "server_code": "mcp.test"})
def remove_mcp_error():
    pass


@_case("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permissions", "lists_mcp_permissions", ExpectSuccess(status=200, json_contains={"data": []}))
def mcp_permissions_happy():
    pass


@_case("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permissions", "missing_set", ExpectError(status=404), path_params={"bot_id": _BOT_ID, "set_id": "999", "server_code": "mcp.test"})
def mcp_permissions_error():
    pass


@_case("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permission-requests", "requests_mcp_permissions", ExpectSuccess(status=200, json_contains={"data": []}), json_body={"reason": "coverage"})
def request_mcp_permissions_happy():
    pass


@_case("POST", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permission-requests", "missing_set", ExpectError(status=404), json_body={"reason": "coverage"}, path_params={"bot_id": _BOT_ID, "set_id": "999", "server_code": "mcp.test"})
def request_mcp_permissions_error():
    pass


@_case("GET", "/openapi/v1/bots/{bot_id}/mcps", "lists_bot_mcps", ExpectSuccess(status=200, json_contains={"data": []}))
def list_bot_mcps_happy():
    pass


@_case("GET", "/openapi/v1/bots/{bot_id}/mcps", "missing_bot", ExpectError(status=404), seed=lambda world: init_principal_verifier_config(_Resolver(), "test-key", strict=False))
def list_bot_mcps_error():
    pass


@_case("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate", "activates_direct_mcp", ExpectSuccess(status=200, json_contains={"data": {"active": True}}), extra=(_assert_reconciled,))
def activate_direct_mcp_happy():
    pass


@_case("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate", "missing_bot", ExpectError(status=404), seed=lambda world: init_principal_verifier_config(_Resolver(), "test-key", strict=False))
def activate_direct_mcp_error():
    pass


@_case("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate", "deactivates_direct_mcp", ExpectSuccess(status=200, json_contains={"data": {"active": False}}), extra=(_assert_reconciled,))
def deactivate_direct_mcp_happy():
    pass


@_case("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate", "missing_bot", ExpectError(status=404), seed=lambda world: init_principal_verifier_config(_Resolver(), "test-key", strict=False))
def deactivate_direct_mcp_error():
    pass
