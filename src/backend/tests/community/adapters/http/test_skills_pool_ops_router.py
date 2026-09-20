"""Skills Pool operator API surface contract."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.skills_pool.router import (
    add_bot_allow,
    enable_environment_rollout,
    enable_owner_rollout,
    get_rollout,
    retired_batch_write,
    rollback_bot,
    router,
    set_engine_admission,
)
from agentclaw.community.adapters.http.skills_pool.schemas import (
    BotPolicyRequest,
    EngineAdmissionRequest,
    OwnerPolicyRequest,
    PolicyMutationRequest,
    RollbackRequest,
)
from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackResult,
)
from agentclaw.community.core.skills_pool.operations import RolloutOperationError
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


def test_all_skills_pool_operations_are_operator_only() -> None:
    expected_new = {
        "/api/ops/skills-pool/rollout/engines/{engine}/admission",
        "/api/ops/skills-pool/rollout/environments/{engine}",
        "/api/ops/skills-pool/rollout/owners/{owner_id}",
        "/api/ops/skills-pool/rollout/bots/{bot_id}/allow",
        "/api/ops/skills-pool/rollout/bots/{bot_id}/exclude",
    }

    paths = {route.path for route in router.routes}
    assert expected_new.issubset(paths)
    assert "/api/ops/skills-pool/batches/{batch_id}" in paths
    for route in router.routes:
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert require_operator in dependency_calls


@pytest.mark.asyncio
async def test_new_policy_routes_forward_engine_revision_and_identity() -> None:
    @dataclass(frozen=True)
    class Result:
        enabled: bool = True

    class RolloutService:
        calls: list[tuple[str, dict[str, object]]]

        def __init__(self) -> None:
            self.calls = []

        def set_engine_admission(self, **kwargs: object):
            self.calls.append(("engine", kwargs))
            return Result()

        def set_environment_rollout(self, **kwargs: object):
            self.calls.append(("environment", kwargs))
            return Result()

        def set_owner_rollout(self, **kwargs: object):
            self.calls.append(("owner", kwargs))
            return Result()

        def set_bot_allow(self, **kwargs: object):
            self.calls.append(("bot", kwargs))
            return Result()

    service = RolloutService()
    user = SimpleNamespace(staffId="freddie")
    mutation = PolicyMutationRequest(
        expected_revision="revision-1",
        reason="controlled rollout",
    )

    await set_engine_admission(
        engine="openclaw",
        request=EngineAdmissionRequest(
            enabled=True,
            expected_revision="revision-1",
            reason="controlled rollout",
        ),
        user=user,
        service=service,
    )
    await enable_environment_rollout(
        engine="openclaw",
        request=mutation,
        user=user,
        service=service,
    )
    await enable_owner_rollout(
        owner_id="168944",
        request=OwnerPolicyRequest(
            engine="openclaw",
            expected_revision="revision-1",
            reason="controlled rollout",
        ),
        user=user,
        service=service,
    )
    await add_bot_allow(
        bot_id="bot-1",
        request=BotPolicyRequest(
            owner_id="168944",
            engine="openclaw",
            expected_revision="revision-1",
            reason="controlled rollout",
        ),
        user=user,
        service=service,
    )

    assert service.calls[0] == (
        "engine",
        {
            "env": "dev",
            "engine": "openclaw",
            "enabled": True,
            "expected_revision": "revision-1",
            "operator": "freddie",
            "reason": "controlled rollout",
        },
    )
    assert service.calls[1][1]["engine"] == "openclaw"
    assert service.calls[2][1]["owner_id"] == "168944"
    assert service.calls[3][1]["bot_id"] == "bot-1"


@pytest.mark.asyncio
async def test_batch_write_routes_are_gone() -> None:
    with pytest.raises(HTTPException) as captured:
        await retired_batch_write(
            _={"batch_id": "batch-1"},
            __=SimpleNamespace(staffId="freddie"),
        )

    assert captured.value.status_code == 410
    assert captured.value.detail == "ROLLOUT_BATCH_API_RETIRED"


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
