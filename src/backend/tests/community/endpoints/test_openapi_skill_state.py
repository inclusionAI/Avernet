"""Endpoint-framework coverage for Local Skill desired-state commands."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.local_skill_state_service import (
    LocalSkillStateServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.local_skill_state_service import (
    LocalSkillStateService,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.skill_installation import (
    SkillInstallationRepositoryProtocol,
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

    def sync_runtime(self) -> bool:
        return self.success

    async def publish_mappings(self, **_kwargs) -> bool:
        return self.success

    async def verify_mappings(self, **_kwargs) -> bool:
        return self.success

    async def project(self, **_kwargs) -> None:
        if not self.success:
            raise RuntimeError("runtime reconcile failed")


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
        skill = world.get(SkillRepository).create(
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
        world.get(SkillSetRepository).add_skill_to_set(
            skill_set["id"], skill["id"], user_id=_OWNER
        )
        world.get(SkillSetRepository).add_default_skill_exclusion(
            _OWNER, _BOT_ID, int(skill_set["id"]), int(skill["id"])
        )
    runtime_factory = _RuntimeFactory(runtime_success)
    world.injector.binder.bind(
        LocalSkillStateServiceProtocol,
        to=LocalSkillStateService(
            world.get(SkillRepository),
            world.get(SkillInstallationRepositoryProtocol),
            world.get(BotRepository),
            world.get(CollaboratorServiceProtocol),
            runtime_factory,
            BotCapabilityStateReader(
                repository=world.get(CapabilityDesiredStateRepositoryProtocol),
                bot_repo=world.get(BotRepository),
                pool_skills=world.get(SkillRepository),
            ),
            world.get(SkillSetRepository),
            runtime_factory._runtime,
        ),
        scope=None,
    )


def _seed_activate(world) -> None:
    _seed_state(world, runtime_success=True)


def _seed_runtime_failure(world) -> None:
    _seed_state(world, runtime_success=False)


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
    scenario="runtime_failure_returns_fixed_error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    expect=ExpectError(
        status=502,
        json_contains={
            "code": 502102,
            "message": "Skill runtime synchronization failed",
            "data": None,
        },
    ),
)
def activate_local_skill_runtime_failure_is_publicly_safe():
    """The activation command maps a runtime transport failure to the fixed envelope."""


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
    scenario="runtime_failure_compensates_with_fixed_error",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    extra_assertions=(_assert_skill_remains_inactive,),
    expect=ExpectError(
        status=502,
        json_contains={
            "code": 502102,
            "message": "Skill runtime synchronization failed",
            "data": None,
        },
    ),
)
def deactivate_local_skill_runtime_failure_is_publicly_safe():
    """The public fixed runtime failure never exposes transport details."""


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
    scenario="legacy_address_maps_runtime_failure_identically",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    expect=ExpectError(
        status=502,
        json_contains={
            "code": 502102,
            "message": "Skill runtime synchronization failed",
            "data": None,
        },
    ),
)
def legacy_activate_runtime_failure_is_publicly_safe():
    """The shim adds no new failure shape of its own."""


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
    scenario="legacy_address_compensates_identically",
    input=CaseInput(
        path_params={"skill_id": "1"},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
    ),
    seed=_seed_runtime_failure,
    extra_assertions=(_assert_skill_remains_inactive,),
    expect=ExpectError(
        status=502,
        json_contains={
            "code": 502102,
            "message": "Skill runtime synchronization failed",
            "data": None,
        },
    ),
)
def legacy_deactivate_runtime_failure_is_publicly_safe():
    """Compensation happens behind the retiring address exactly as behind the new one."""
