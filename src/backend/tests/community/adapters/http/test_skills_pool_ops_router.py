"""Skills Pool operator API surface contract."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.skills_pool.router import (
    get_rollout,
    rollback_bot,
    router,
)
from agentclaw.community.adapters.http.skills_pool.schemas import RollbackRequest
from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackResult,
)
from agentclaw.community.core.skills_pool.operations import RolloutOperationError
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


def test_all_skills_pool_operations_are_operator_only() -> None:
    expected = {
        "/api/ops/skills-pool/rollout",
        "/api/ops/skills-pool/rollout/feature",
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
async def test_rollback_route_supplies_a_unique_lease_owner() -> None:
    scope = BotSkillLayoutScope("pre", "entity-1", "bot-1")

    class Query:
        def get_bot(self, **_: object):
            return SimpleNamespace(scope=scope)

    class RollbackService:
        call: dict[str, object] | None = None

        async def rollback(self, **kwargs: object) -> SkillsPoolRollbackResult:
            self.call = kwargs
            return SkillsPoolRollbackResult(
                SkillsPoolRollbackOutcome.LEGACY_ACTIVE
            )

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
