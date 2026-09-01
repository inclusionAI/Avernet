"""Endpoint-framework coverage for Local Skill desired-state commands."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.direct_activation_service import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
)
from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "state-owner"
_BOT_ID = "state-bot"
_TENANT = "state-tenant"
_KEY = "state-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Runtime:
    def __init__(self, success: bool) -> None:
        self.success = success

    async def project_skills(self) -> bool:
        return self.success

    async def publish_mappings(self, **_kwargs) -> bool:
        return self.success

    async def verify_mappings(self, **_kwargs) -> bool:
        return self.success

    async def project(self, **_kwargs) -> None:
        if not self.success:
            raise RuntimeError("runtime reconcile failed")

    async def snapshot_skill_mappings(self, **_kwargs):
        return ()


class _RuntimeFactory:
    def __init__(self, success: bool) -> None:
        self._runtime = _Runtime(success)

    def create(self, **_kwargs) -> _Runtime:
        return self._runtime


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
                    "subject": {"id": _OWNER, "username": "state@example.test"},
                },
                {
                    "type": "app",
                    "tenant": _TENANT,
                    "app": {
                        "app_id": 1,
                        "app_name": "State Test App",
                        "owners": "state-org",
                        "tenant": _TENANT,
                    },
                },
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _seed_state(world, *, runtime_success: bool) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "State Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        world.get(SkillSetRepository).create(
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
        # A direct-controlled Local Skill: no Set membership. A Set-managed
        # member — the Default included, excluded or not — refuses the
        # Skill-level command (R1, no exclusion carve-out); the dedicated
        # 409 case below pins that.
        world.get(SkillRepository).create(
            {
                "name": "state-skill",
                "description": "State endpoint coverage",
                "git_path": "local://state-skill",
                "category": "general",
                "tags": "[]",
                "is_public": False,
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "source_type": "upload",
            }
        )
    runtime_factory = _RuntimeFactory(runtime_success)
    world.injector.binder.bind(
        DirectActivationServiceProtocol,
        to=DirectActivationService(
            world.get(CapabilityDesiredStateRepositoryProtocol),
            world.get(BotRepository),
            world.get(SkillRepository),
            runtime_factory._runtime,
            world.get(BotCapabilityAuthorizationHookProtocol),
            world.get(BotCollabLogRepositoryProtocol),
            world.get(MCPCenterPlugin),
            BotCapabilityStateReader(
                repository=world.get(CapabilityDesiredStateRepositoryProtocol),
                bot_repo=world.get(BotRepository),
                pool_skills=world.get(SkillRepository),
                version_resolver=PassthroughSkillVersionResolver(),
            ),
            PlatformDefaultMcpPolicy(lambda _bot_id: None),
        ),
        scope=None,
    )


def _seed_activate(world) -> None:
    _seed_state(world, runtime_success=True)


def _seed_runtime_failure(world) -> None:
    _seed_state(world, runtime_success=False)


def _seed_excluded_default_member(world) -> None:
    """The Bot's Default Set holds the Skill, and the owner excluded it."""
    _seed_state(world, runtime_success=True)
    with avernet_tenant_scope(_TENANT):
        sets = world.get(SkillSetRepository)
        default_set = sets.get_default(user_id=_OWNER, bolt_id=_BOT_ID)
        sets.add_skill_to_set(default_set["id"], "1", user_id=_OWNER)
        sets.add_default_skill_exclusion(_OWNER, _BOT_ID, int(default_set["id"]), 1)


def _assert_skill_remains_inactive(_response, world) -> None:
    with avernet_tenant_scope(_TENANT):
        skill = world.get(SkillRepository).get_bot_local_skill(
            skill_id="1", bot_id=_BOT_ID, user_id=_OWNER
        )
    assert skill is not None and skill["active"] is False


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
    scenario="activates_exact_tenant_local_skill",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_activate,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"changed": True, "skill": {"active": True}},
        },
    ),
)
def activate_local_skill_reconciles_runtime():
    """The real public router, tenant guard, Core service, and repositories activate one Skill."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
    scenario="runtime_failure_keeps_committed_state_and_reports_pending",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "desired_state": {"status": "COMMITTED"},
                "runtime_projection": {"status": "PENDING"},
            },
        },
    ),
)
def activate_local_skill_runtime_failure_is_publicly_safe():
    """The activation command keeps Desired State and reports a safe Pending result."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
    scenario="excluded_default_member_refuses_direct_control",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_excluded_default_member,
    expect=ExpectError(
        status=409,
        json_contains={"code": 409202},
    ),
)
def excluded_default_member_stays_set_managed():
    """R1 with no exclusion carve-out: re-activation removes the exclusion
    through the Set wire, never the Skill-level command."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    scenario="idempotent_inactive_skill_still_reconciles",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_activate,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"changed": False, "skill": {"active": False}},
        },
    ),
)
def deactivate_inactive_local_skill_is_an_idempotent_happy_path():
    """A repeated desired state remains a successful synchronous reconciliation."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    scenario="runtime_failure_keeps_deactivated_state_and_reports_pending",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    extra_assertions=(_assert_skill_remains_inactive,),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"runtime_projection": {"status": "PENDING"}},
        },
    ),
)
def deactivate_local_skill_runtime_failure_is_publicly_safe():
    """The public Pending result never exposes runtime transport details."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    scenario="set_managed_skill_refuses_direct_control",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_excluded_default_member,
    expect=ExpectError(status=409, json_contains={"code": 409202}),
)
def deactivate_set_managed_skill_is_refused():
    """A Set-managed Skill can only be changed through its SkillSet command."""


# The retiring addresses. `POST /openapi/v1/bots/skills/{skill_id}/activate`
# names no bot at all, so the shim in `openapi_v1/deprecated/skills.py` reads
# the skill record, resolves the bot behind it, and re-checks the grant against
# that pair before delegating. Nothing about that is exercised by driving the
# current address, so both verbs are driven here on their own.


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/{skill_id}/activate",
    scenario="legacy_address_activates_the_same_skill",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_activate,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"changed": True, "skill": {"active": True}},
        },
    ),
)
def legacy_activate_resolves_the_bot_from_the_skill():
    """The bot the address does not name is found behind the skill id."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/{skill_id}/activate",
    scenario="legacy_address_reports_runtime_pending_without_failing_command",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"runtime_projection": {"status": "PENDING"}},
        },
    ),
)
def legacy_activate_runtime_failure_is_publicly_safe():
    """The shim keeps the canonical Pending result shape."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/{skill_id}/activate",
    scenario="legacy_address_refuses_set_managed_direct_control",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_excluded_default_member,
    expect=ExpectError(status=409, json_contains={"code": 409202}),
)
def legacy_activate_set_managed_skill_is_refused():
    """The legacy address preserves the canonical ownership restriction."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/{skill_id}/deactivate",
    scenario="legacy_address_is_idempotent_too",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_activate,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"changed": False, "skill": {"active": False}},
        },
    ),
)
def legacy_deactivate_inactive_local_skill_is_an_idempotent_happy_path():
    """A repeated desired state is still a successful reconciliation here."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/{skill_id}/deactivate",
    scenario="legacy_address_keeps_committed_state_when_runtime_is_pending",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    extra_assertions=(_assert_skill_remains_inactive,),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"runtime_projection": {"status": "PENDING"}},
        },
    ),
)
def legacy_deactivate_runtime_failure_is_publicly_safe():
    """The retiring address also keeps Desired State on Runtime drift."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/{skill_id}/deactivate",
    scenario="legacy_address_refuses_set_managed_direct_control",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_excluded_default_member,
    expect=ExpectError(status=409, json_contains={"code": 409202}),
)
def legacy_deactivate_set_managed_skill_is_refused():
    """The legacy address preserves the canonical ownership restriction."""
