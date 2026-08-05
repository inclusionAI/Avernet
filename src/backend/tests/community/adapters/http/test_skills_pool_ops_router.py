"""Skills Pool operator API surface contract."""

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.skills_pool.router import (
    get_rollout,
    rollback_bot,
    router,
    set_full_rollout,
    set_owner_full_rollout,
    set_rollout_feature,
)
from agentclaw.community.adapters.http.skills_pool.schemas import (
    ControlBotRequest,
    FeatureToggleRequest,
    FullRolloutRequest,
    OwnerFullRolloutRequest,
    RollbackRequest,
)
from agentclaw.community.api.skills_pool_rollout_service import (
    SkillsPoolRolloutServiceProtocol,
)
from agentclaw.community.core.common_config.repository import (
    CommonConfigRepository,
)
from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackResult,
)
from agentclaw.community.core.skills_pool.operations import RolloutOperationError
from agentclaw.community.core.skills_pool.rollout_gate import (
    SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
    SKILLS_POOL_ROLLOUT_PARAM_CODE,
)
from agentclaw.community.core.skills_pool.rollout_repository import (
    SkillsPoolRolloutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.plugin_api.database import DatabasePlugin


def test_all_skills_pool_operations_are_operator_only() -> None:
    expected = {
        "/api/ops/skills-pool/rollout",
        "/api/ops/skills-pool/rollout/feature",
        "/api/ops/skills-pool/rollout/full",
        "/api/ops/skills-pool/rollout/owners",
        "/api/ops/skills-pool/rollout/promote",
        "/api/ops/skills-pool/rollout/whitelist",
        "/api/ops/skills-pool/rollout/whitelist/remove",
        "/api/ops/skills-pool/rollout/batches/accept",
        "/api/ops/skills-pool/rollout/controls",
        "/api/ops/skills-pool/bots/{bot_id}",
        "/api/ops/skills-pool/batches/{batch_id}",
        "/api/ops/skills-pool/bots/{bot_id}/wake",
        "/api/ops/skills-pool/bots/{bot_id}/retry",
        "/api/ops/skills-pool/bots/{bot_id}/repair",
        "/api/ops/skills-pool/bots/{bot_id}/rollback",
    }

    assert {route.path for route in router.routes} == expected
    for route in router.routes:
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert require_operator in dependency_calls


@pytest.mark.asyncio
async def test_feature_post_normalizes_legacy_config_and_audits_once(
    test_injector,
) -> None:
    database = test_injector.get(DatabasePlugin)
    await database.bootstrap()
    configs = CommonConfigRepository(database)
    config_id = configs.create_config(
        business_code=SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
        business_name="Skills Pool",
        param_code=SKILLS_POOL_ROLLOUT_PARAM_CODE,
        param_name="Skills Pool layout rollout",
        param_value=json.dumps(
            {
                "enable_all": False,
                "promoted_engines": ["openclaw"],
                "whitelist": [],
                "negative_controls": [],
                "teclaw_controls": [],
            }
        ),
        enable="0",
        ext_info=json.dumps({"revision": "legacy-revision"}),
        env="dev",
    )
    service = test_injector.get(SkillsPoolRolloutServiceProtocol)

    response = await set_rollout_feature(
        request=FeatureToggleRequest(
            enabled=True,
            reason="resume pre canary",
        ),
        user=SimpleNamespace(staffId="freddie"),
        service=service,
    )
    repeated = await set_rollout_feature(
        request=FeatureToggleRequest(
            enabled=True,
            reason="idempotent retry",
        ),
        user=SimpleNamespace(staffId="freddie"),
        service=service,
    )

    assert response.success is True
    assert repeated.success is True
    assert response.data["enabled"] is True
    assert response.data["enable_all"] is False
    assert response.data["full_rollout_engines"] == ()
    assert response.data["full_rollout_owners"] == ()
    stored = configs.get_by_id(config_id=config_id)
    assert stored is not None
    assert json.loads(stored.param_value or "{}") == {
        "enable_all": False,
        "full_rollout_engines": [],
        "full_rollout_owners": [],
        "promoted_engines": ["openclaw"],
        "whitelist": [],
        "negative_controls": [],
        "teclaw_controls": [],
    }
    audit = test_injector.get(SkillsPoolRolloutRepositoryProtocol).list_audit_events(
        env="dev"
    )
    assert [event["action"] for event in audit] == ["enable"]


def test_control_bot_request_rejects_empty_batch_id() -> None:
    with pytest.raises(ValidationError):
        ControlBotRequest(
            owner_id="owner-1",
            bot_id="bot-1",
            batch_id="",
            group="negative",
            reason="control sample",
        )


@pytest.mark.asyncio
async def test_full_rollout_route_forwards_optional_engine() -> None:
    @dataclass(frozen=True)
    class Result:
        enabled: bool = True

    class RolloutService:
        call: dict[str, object] | None = None

        def set_full_rollout(self, **kwargs: object):
            self.call = kwargs
            return Result()

    service = RolloutService()

    await set_full_rollout(
        request=FullRolloutRequest(
            enabled=True,
            engine="openclaw",
            reason="promote future OpenClaw claims",
        ),
        user=SimpleNamespace(staffId="freddie"),
        service=service,
    )

    assert service.call == {
        "env": "dev",
        "enabled": True,
        "engine": "openclaw",
        "operator": "freddie",
        "reason": "promote future OpenClaw claims",
    }


@pytest.mark.asyncio
async def test_owner_full_rollout_route_forwards_owner_engine_and_acceptance() -> None:
    @dataclass(frozen=True)
    class Result:
        enabled: bool = True

    class RolloutService:
        call: dict[str, object] | None = None

        def set_owner_full_rollout(self, **kwargs: object):
            self.call = kwargs
            return Result()

    service = RolloutService()

    await set_owner_full_rollout(
        request=OwnerFullRolloutRequest(
            owner_id="168944",
            engine="openclaw",
            enabled=True,
            acceptance_batch_id="openclaw-canary-1",
            reason="enable all owner bots in pre",
        ),
        user=SimpleNamespace(staffId="freddie"),
        service=service,
    )

    assert service.call == {
        "env": "dev",
        "owner_id": "168944",
        "engine": "openclaw",
        "enabled": True,
        "acceptance_batch_id": "openclaw-canary-1",
        "operator": "freddie",
        "reason": "enable all owner bots in pre",
    }


@pytest.mark.asyncio
async def test_rollback_route_supplies_a_unique_lease_owner() -> None:
    scope = BotSkillLayoutScope("pre", "entity-1", "bot-1")

    class Query:
        def get_bot(self, **_: object):
            return SimpleNamespace(scope=scope)

    class RollbackService:
        call: dict[str, object] | None = None

        async def rollback(self, **kwargs: object) -> SkillsPoolRollbackResult:
            self.call = kwargs
            return SkillsPoolRollbackResult(SkillsPoolRollbackOutcome.LEGACY_ACTIVE)

    service = RollbackService()

    await rollback_bot(
        bot_id="bot-1",
        request=RollbackRequest(
            owner_id="owner-1",
            rollback_generation="rollback-1",
            note="operator confirmed",
        ),
        user=SimpleNamespace(staffId="freddie"),
        service=service,
        query=Query(),
    )

    assert service.call is not None
    assert service.call["scope"] == scope
    assert service.call["operator"] == "freddie"
    assert str(service.call["lease_owner"]).startswith("operator-api:")


@pytest.mark.asyncio
async def test_invalid_rollout_config_is_an_explicit_operator_conflict() -> None:
    class InvalidConfig:
        def get_snapshot(self, **_: object):
            raise RolloutOperationError("rollout config is invalid")

    with pytest.raises(HTTPException) as captured:
        await get_rollout(
            _=SimpleNamespace(staffId="freddie"),
            service=InvalidConfig(),
        )

    assert captured.value.status_code == 409
