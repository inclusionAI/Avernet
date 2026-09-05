"""Endpoint-framework coverage for every canonical SkillSet operation.

The cases use the production control-plane service and SQLite repositories.  A
recording runtime is the only substituted boundary, so the tests exercise the
same ACL, desired-state UoW, and router seam as a real request.
"""

from __future__ import annotations

from datetime import UTC, datetime
import time
from types import SimpleNamespace

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.api.skill_center_reference_service import (
    ReferenceNotFoundError,
    SkillCenterReferenceBatch,
    SkillCenterReferenceItem,
    SkillCenterReferencePage,
    SkillCenterReferenceServiceProtocol,
    SkillCenterReferenceStatus,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
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

    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, *, bot_id: str, owner_id: str, **_kwargs) -> None:
        self.calls.append((bot_id, owner_id))

    def resolve_plan(self, *, bot_id: str, owner_id: str, **_kwargs):
        return SimpleNamespace(
            bot_id=bot_id,
            owner_id=owner_id,
            projection=SimpleNamespace(skill_mappings=()),
        )

    async def apply_plan(self, *, plan, **kwargs) -> None:
        await self.project(
            bot_id=plan.bot_id,
            owner_id=plan.owner_id,
            **kwargs,
        )


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
        repository = world.get(CapabilityDesiredStateRepositoryProtocol)
        skill_set = repository.create_set(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            name="Endpoint set",
            description=None,
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
    control_plane = SkillSetManagementService(
        repository=world.get(CapabilityDesiredStateRepositoryProtocol),
        bot_repo=world.get(BotRepository),
        runtime=runtime,
        legacy_factory=world.get(SkillSetServiceFactory),
        passport=world.get(PassportPlugin),
        authorization=world.get(BotCapabilityAuthorizationHookProtocol),
        audit_log_repo=world.get(BotCollabLogRepositoryProtocol),
        mcp_center=world.get(MCPCenterPlugin),
        mcp_auth=world.get(MCPAuthPlugin),
        ext_info_provider=lambda _bot_id: None,
    )
    world.injector.binder.bind(
        SkillSetManagementServiceProtocol, to=control_plane, scope=None
    )
    # The direct-activation routes share the recording runtime, so
    # ``_assert_reconciled`` observes their projection the same way.
    direct = DirectActivationService(
        world.get(CapabilityDesiredStateRepositoryProtocol),
        world.get(BotRepository),
        world.get(SkillRepository),
        runtime,
        world.get(BotCapabilityAuthorizationHookProtocol),
        world.get(BotCollabLogRepositoryProtocol),
        world.get(MCPCenterPlugin),
        world.get(BotCapabilityStateReaderProtocol),
        PlatformDefaultMcpPolicy(lambda _bot_id: None),
    )
    world.injector.binder.bind(
        DirectActivationServiceProtocol, to=direct, scope=None
    )


def _seed_member(world) -> None:
    _seed(world, member=True)


def _seed_active(world) -> None:
    _seed_member(world)
    with avernet_tenant_scope(_TENANT):
        world.get(CapabilityDesiredStateRepositoryProtocol).set_skill_set_active(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            set_id="1",
            active=True,
            engine_type="openclaw",
        )


def _seed_inactive(world) -> None:
    """Seed a Set and switch it off — the only shape DELETE accepts.

    ``create_set`` persists ``is_active=True``, and an active Set cannot be
    deleted (that is ``rejects_active_set``, the error case next door). So the
    happy path deactivates first rather than relying on how a Set is born.
    """
    _seed(world)
    with avernet_tenant_scope(_TENANT):
        world.get(CapabilityDesiredStateRepositoryProtocol).set_skill_set_active(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            set_id="1",
            active=False,
            engine_type="openclaw",
        )


def _assert_reconciled(_response, world) -> None:
    assert world.get(SkillSetManagementServiceProtocol)._runtime.calls == [
        (_BOT_ID, _OWNER)
    ]


class _ReferenceService:
    @staticmethod
    def _item() -> SkillCenterReferenceItem:
        now = datetime(2026, 8, 30, tzinfo=UTC)
        return SkillCenterReferenceItem(
            reference_id="reference-1",
            request_id="request-1",
            skill_set_id="1",
            skill_code="public-skill",
            sc_version_number=None,
            status=SkillCenterReferenceStatus.QUEUED,
            skill_id=None,
            error_code=None,
            error_message=None,
            gmt_created=now,
            gmt_modified=now,
        )

    def create(self, **kwargs) -> SkillCenterReferenceBatch:
        return SkillCenterReferenceBatch(
            request_id="request-1",
            bot_id=kwargs["bot_id"],
            owner_id=kwargs["owner_id"],
            skill_set_id=kwargs["skill_set_id"],
            actor_id=kwargs["actor_id"],
            items=(self._item(),),
        )

    def list(self, **_kwargs) -> SkillCenterReferencePage:
        return SkillCenterReferencePage(total=1, items=(self._item(),))

    def get(self, **kwargs) -> SkillCenterReferenceItem:
        if kwargs["reference_id"] != "reference-1":
            raise ReferenceNotFoundError("not found")
        return self._item()


def _seed_reference(world) -> None:
    _seed(world)
    world.injector.binder.bind(
        SkillCenterReferenceServiceProtocol,
        to=_ReferenceService(),
        scope=None,
    )


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
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    "accepts_reference_batch",
    ExpectSuccess(
        status=202,
        json_contains={
            "code": 202000,
            "data": {
                "request_id": "request-1",
                "reference_ids": ["reference-1"],
            },
        },
    ),
    seed=_seed_reference,
    headers={**_HEADERS, "Idempotency-Key": "reference-command"},
    json_body={"skill_codes": ["public-skill"]},
)
def create_references_happy():
    pass


@_case(
    "POST",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    "missing_idempotency_key",
    ExpectError(status=422),
    seed=_seed_reference,
    json_body={"skill_codes": ["public-skill"]},
)
def create_references_error():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    "lists_persisted_references",
    ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [
                    {"reference_id": "reference-1", "status": "QUEUED"}
                ],
            },
        },
    ),
    seed=_seed_reference,
)
def list_references_happy():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    "unauthenticated",
    ExpectError(status=401),
    seed=_seed_reference,
    headers={},
)
def list_references_error():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}",
    "gets_persisted_reference",
    ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"reference_id": "reference-1", "skill_code": "public-skill"},
        },
    ),
    seed=_seed_reference,
    path_params={
        "bot_id": _BOT_ID,
        "set_id": "1",
        "reference_id": "reference-1",
    },
)
def get_reference_happy():
    pass


@_case(
    "GET",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}",
    "missing_reference",
    ExpectError(status=404),
    seed=_seed_reference,
    path_params={
        "bot_id": _BOT_ID,
        "set_id": "1",
        "reference_id": "missing",
    },
)
def get_reference_error():
    pass


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
    "creates_active_set",
    ExpectSuccess(status=201, json_contains={"data": {"is_active": True}}),
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
    seed=_seed_inactive,
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
        world.get(CapabilityDesiredStateRepositoryProtocol).add_mcp(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            set_id="1",
            server_code="mcp.test",
            name="Test MCP",
            description=None,
            icon=None,
            engine_type="openclaw",
        )


def _seed_mcp_catalog(world) -> None:
    world.get(MCPCenterPlugin).set_override(
        "get_mcp_detail",
        lambda server_code: {
            "serverCode": server_code,
            "name": "Dima MCP",
            "description": "Dima workflow tools",
            "icon": "https://example.test/dima.png",
        },
    )
    _seed(world)


def _assert_mcp_catalog_metadata_persisted(_response, world) -> None:
    # Read through the control-plane service, whose list_mcps hands back the
    # membership rows verbatim — so this asserts what was persisted, not what
    # the request echoed back.
    with avernet_tenant_scope(_TENANT):
        assert world.get(SkillSetManagementServiceProtocol).list_mcps(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            user_id=_OWNER,
            set_id="1",
        ) == [
            {
                "id": "1",
                "server_code": "mcp.test",
                "name": "Dima MCP",
                "description": "Dima workflow tools",
                "icon": "https://example.test/dima.png",
            }
        ]


def _assert_no_mcp_membership(_response, world) -> None:
    with avernet_tenant_scope(_TENANT):
        assert world.get(SkillSetManagementServiceProtocol).list_mcps(
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            user_id=_OWNER,
            set_id="1",
        ) == []


@_case("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps", "lists_mcps", ExpectSuccess(status=200, json_contains={"data": []}))
def list_mcps_happy():
    pass


@_case("GET", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps", "missing_set", ExpectError(status=404), path_params={"bot_id": _BOT_ID, "set_id": "999", "server_code": "mcp.test"})
def list_mcps_error():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    "adds_mcp_with_catalog_metadata",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
    seed=_seed_mcp_catalog,
    extra=(_assert_mcp_catalog_metadata_persisted,),
)
def add_mcp_happy():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    "rejects_missing_mcp_catalog_entry",
    ExpectError(status=404),
    path_params={
        "bot_id": _BOT_ID,
        "set_id": "1",
        "server_code": "mcp.unknown",
    },
    extra=(_assert_no_mcp_membership,),
)
def add_missing_mcp_error():
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


# ── Default-Set exclusion wire (restored opt-out, spec E.11) ─────────


_DEFAULT_SET_PARAMS = {
    "bot_id": _BOT_ID,
    "set_id": "2",
    "skill_id": "1",
    "server_code": "mcp.default",
}


def _seed_default_member(world) -> None:
    """The Bot's own Default Set holding skill 1 and one MCP, both flushed."""
    from agentclaw.community.core.models.mcp import (
        BotMCPInstallation,
        SkillSetMCPServer,
    )
    from agentclaw.community.core.models.skill import (
        BotSkillInstallation,
        SkillSet,
        SkillSetSkill,
    )
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.utils.env_utils import get_current_env

    _seed(world)
    with avernet_tenant_scope(_TENANT):
        env = get_current_env()
        with world.get(DatabasePlugin).transactional_orm_session() as session:
            default = SkillSet(
                name="Default",
                user_id=_OWNER,
                bolt_id=_BOT_ID,
                engine_type="openclaw",
                is_default=True,
                is_active=True,
                env=env,
            )
            session.add(default)
            session.flush()
            assert str(default.id) == _DEFAULT_SET_PARAMS["set_id"]
            session.add_all(
                [
                    SkillSetSkill(
                        skill_set_id=default.id, skill_id=1, env=env
                    ),
                    SkillSetMCPServer(
                        skill_set_id=default.id,
                        server_code="mcp.default",
                        name="Default MCP",
                        env=env,
                    ),
                    BotSkillInstallation(
                        bot_id=_BOT_ID, owner_id=_OWNER, skill_id=1, env=env
                    ),
                    BotMCPInstallation(
                        bot_id=_BOT_ID,
                        owner_id=_OWNER,
                        server_code="mcp.default",
                        env=env,
                    ),
                ]
            )


def _seed_excluded_default_member(world) -> None:
    _seed_default_member(world)
    with avernet_tenant_scope(_TENANT):
        world.get(CapabilityDesiredStateRepositoryProtocol).exclude_default_skill(
            bot_id=_BOT_ID, owner_id=_OWNER, set_id="2", skill_id="1",
            engine_type="openclaw", default_engine_types=("openclaw",),
        )


def _seed_excluded_default_mcp(world) -> None:
    _seed_default_member(world)
    with avernet_tenant_scope(_TENANT):
        world.get(CapabilityDesiredStateRepositoryProtocol).exclude_default_mcp(
            bot_id=_BOT_ID, owner_id=_OWNER, set_id="2",
            server_code="mcp.default",
            engine_type="openclaw", default_engine_types=("openclaw",),
        )


def _excluded_skill_ids(world) -> set[int]:
    with avernet_tenant_scope(_TENANT):
        return world.get(
            CapabilityDesiredStateRepositoryProtocol
        ).excluded_default_skill_ids(bot_id=_BOT_ID, owner_id=_OWNER, set_id="2")


def _excluded_mcp_codes(world) -> set[str]:
    with avernet_tenant_scope(_TENANT):
        return world.get(
            CapabilityDesiredStateRepositoryProtocol
        ).excluded_default_mcp_codes(bot_id=_BOT_ID, owner_id=_OWNER, set_id="2")


def _assert_skill_excluded(_response, world) -> None:
    assert _excluded_skill_ids(world) == {1}
    _assert_reconciled(_response, world)


def _assert_skill_unexcluded(_response, world) -> None:
    assert _excluded_skill_ids(world) == set()
    _assert_reconciled(_response, world)


def _assert_mcp_excluded(_response, world) -> None:
    assert _excluded_mcp_codes(world) == {"mcp.default"}
    _assert_reconciled(_response, world)


def _assert_mcp_unexcluded(_response, world) -> None:
    assert _excluded_mcp_codes(world) == set()
    _assert_reconciled(_response, world)


@_case(
    "DELETE",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "excludes_default_member",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
    seed=_seed_default_member,
    path_params=_DEFAULT_SET_PARAMS,
    extra=(_assert_skill_excluded,),
)
def remove_default_member_excludes():
    """Removing a Default-Set member writes the per-Bot exclusion (E.11)."""


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "unexcludes_default_member",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
    seed=_seed_excluded_default_member,
    path_params=_DEFAULT_SET_PARAMS,
    extra=(_assert_skill_unexcluded,),
)
def add_excluded_default_member_unexcludes():
    """Adding an excluded member back removes the exclusion, never the API."""


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}",
    "default_membership_stays_immutable",
    ExpectError(status=409, json_contains={"code": 409204}),
    seed=_seed_default_member,
    path_params=_DEFAULT_SET_PARAMS,
)
def add_new_default_member_refused():
    """A non-excluded skill cannot join the Default Set."""


@_case(
    "DELETE",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    "excludes_default_mcp",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
    seed=_seed_default_member,
    path_params=_DEFAULT_SET_PARAMS,
    extra=(_assert_mcp_excluded,),
)
def remove_default_mcp_excludes():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    "unexcludes_default_mcp",
    ExpectSuccess(status=200, json_contains={"data": {"changed": True}}),
    seed=_seed_excluded_default_mcp,
    path_params=_DEFAULT_SET_PARAMS,
    extra=(_assert_mcp_unexcluded,),
)
def add_excluded_default_mcp_unexcludes():
    pass


@_case(
    "PUT",
    "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}",
    "default_mcp_membership_stays_immutable",
    ExpectError(status=409, json_contains={"code": 409204}),
    seed=_seed_default_member,
    path_params={**_DEFAULT_SET_PARAMS, "server_code": "mcp.never-member"},
)
def add_new_default_mcp_refused():
    pass
