"""Runtime receipts through the shared capability reconciliation service."""
import asyncio
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
from agentclaw.community.plugin_api.models import BotModel
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


@pytest.mark.asyncio
async def test_unknown_artifact_receipt_waits_for_a_different_device(tmp_path):
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )

    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    address = {"bot_id": "bot", "entity_id": "owner"}
    repository = CapabilityProjectionRepository(db)
    projector = _Projector([
        RuntimeProjectionResult.pending(
            code="ARTIFACT_APPLY_FAILED",
            reason="The Runtime did not confirm artifact delivery",
            delivery_uncertain=True,
        ),
        RuntimeProjectionResult.converged(),
    ])
    reconciler = CapabilityProjectionReconciler(
        repository=repository,
        projector=projector,
        guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=BotRepository(db),
        defer_enabled=True,
    )

    def bind(binding_id, device_id):
        with db.transactional_orm_session() as session:
            row = session.query(BotModel).filter_by(**address).one()
            row.active_engine = "teclaw"
            row.binding_id = binding_id
            row.device_id = device_id

    bind(1, "device-1")
    revision, result = await reconciler.finish_apply(**address, initialize=True)
    assert result.status is RuntimeProjectionStatus.PENDING
    assert len(projector.scopes) == 1
    assert any(issue.code == "CAPABILITY_DELIVERY_OUTCOME_UNKNOWN" for issue in result.issues)

    bind(2, "device-1")
    result = await reconciler.reconcile(**address)
    assert result.status is RuntimeProjectionStatus.PENDING
    assert len(projector.scopes) == 1

    bind(3, "device-2")
    result = await reconciler.reconcile(**address)
    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert len(projector.scopes) == 2
    assert reconciler.observe(**address, revision=revision).reason == "CAPABILITY_PROJECTION_SUPERSEDED"


@pytest.mark.asyncio
async def test_committed_edit_during_delivery_never_inherits_the_older_receipt(tmp_path):
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )

    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    address = {"bot_id": "bot", "entity_id": "owner"}
    repository = CapabilityProjectionRepository(db)
    sending = asyncio.Event()
    release = asyncio.Event()

    class Projector(_Projector):
        async def apply_plan(self, *, plan, retired_mappings, scope):
            sending.set()
            await release.wait()
            return await super().apply_plan(plan=plan, retired_mappings=retired_mappings, scope=scope)

    projector = Projector([RuntimeProjectionResult.converged()] * 2)
    reconciler = CapabilityProjectionReconciler(
        repository=repository,
        projector=projector,
        guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=BotRepository(db),
        defer_enabled=True,
    )
    first = asyncio.create_task(reconciler.finish_apply(**address, initialize=True))
    await sending.wait()
    edited = repository.require(
        **address,
        scope=ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.removed"})),
    )
    release.set()
    old_revision, result = await first
    assert old_revision != edited.revision
    assert result.status is RuntimeProjectionStatus.PENDING
    assert reconciler.observe(**address, revision=edited.revision).status is RuntimeProjectionStatus.PENDING
    assert (await reconciler.reconcile(**address)).status is RuntimeProjectionStatus.CONVERGED
    assert projector.scopes[-1].released_mcp == frozenset({"mcp.removed"})
