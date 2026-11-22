"""Runtime receipts through the shared capability reconciliation service."""
from types import SimpleNamespace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.capability_projection import (
    CapabilityProjectionRepository,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.capability_delivery_guard import (
    CapabilityDeliveryGuard,
)
from agentclaw.community.plugins.local.cache import MemoryCachePlugin
from tests.community.repository.skill_center.capability_desired_state_support import _Database


class _Projector:
    def __init__(self, results):
        self._results = iter(results)
        self.scopes = []

    def resolve_plan(self, **kwargs):
        return SimpleNamespace(
            bot={"status": "ACTIVE"},
            projection=SimpleNamespace(skill_mappings=[]),
        )

    async def snapshot_skill_mappings(self, **kwargs):
        return ()

    async def apply_plan(self, *, plan, retired_mappings, scope):
        self.scopes.append(scope)
        return next(self._results)


@pytest.mark.asyncio
async def test_recovery_projects_only_the_failed_domain_then_keeps_converged_baseline(tmp_path):
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )

    address = {"bot_id": "bot", "entity_id": "owner"}
    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    repository = CapabilityProjectionRepository(db)
    projector = _Projector(
        [
            RuntimeProjectionResult.combine(
                RuntimeProjectionResult.converged(
                    components={"skills": RuntimeProjectionStatus.CONVERGED}
                ),
                RuntimeProjectionResult.pending(code="MCP_OFFLINE", reason="MCP device offline"),
            ),
            RuntimeProjectionResult.converged(
                components={"mcp": RuntimeProjectionStatus.CONVERGED}
            ),
        ]
    )
    reconciler = CapabilityProjectionReconciler(
        repository=repository,
        projector=projector,
        guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=BotRepository(db),
        defer_enabled=True,
    )

    async with reconciler.deferred_apply(**address, owner_id="owner"):
        revision, result = await reconciler.finish_apply(**address, initialize=True)
    assert result.status is RuntimeProjectionStatus.PENDING
    assert projector.scopes == [ProjectionScope.everything()]

    result = await reconciler.reconcile(**address)
    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert len(projector.scopes) == 2
    assert projector.scopes[1].mcp and not projector.scopes[1].skills
    assert reconciler.observe(**address, revision=revision).components == {
        "skills": RuntimeProjectionStatus.CONVERGED,
        "mcp": RuntimeProjectionStatus.CONVERGED,
    }

    next_revision, result = await reconciler.finish_apply(**address, initialize=True)
    assert next_revision == revision
    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert len(projector.scopes) == 2
