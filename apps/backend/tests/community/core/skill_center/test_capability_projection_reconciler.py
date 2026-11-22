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


@pytest.mark.asyncio
async def test_enabled_first_apply_cannot_drop_a_competing_send_before_baseline(tmp_path):
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )

    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    address = {"bot_id": "bot", "entity_id": "owner"}
    repository = CapabilityProjectionRepository(db)
    cache = MemoryCachePlugin()
    projector = _Projector([RuntimeProjectionResult.converged()])
    holder = CapabilityProjectionReconciler(
        repository=repository, projector=projector, guard=CapabilityDeliveryGuard(cache),
        bots=BotRepository(db), defer_enabled=True,
    )
    contender = CapabilityProjectionReconciler(
        repository=repository, projector=projector, guard=CapabilityDeliveryGuard(cache),
        bots=BotRepository(db), defer_enabled=True,
    )
    scope = ProjectionScope(mcp=True, updated_mcp=frozenset({"mcp.changed"}))

    async def legacy():
        pytest.fail("Enabled competing delivery bypassed the capability fence")

    submitted = asyncio.Event()
    async def compete():
        submitted.set()
        return await contender.project_competing_change(
            **address, owner_id="owner", scope=scope, retired_mappings=(), legacy=legacy,
        )

    async with holder.deferred_apply(**address, owner_id="owner"):
        assert repository.read(**address) is None
        pending = asyncio.create_task(compete())
        await submitted.wait()
        for _ in range(30):
            state = await asyncio.to_thread(repository.read, **address)
            if state is not None:
                break
            await asyncio.sleep(0.01)
        assert state is not None
        assert state.scope.updated_mcp == frozenset({"mcp.changed"})
        assert not projector.scopes
    assert (await pending).status is RuntimeProjectionStatus.CONVERGED


@pytest.mark.asyncio
async def test_busy_lifecycle_refresh_stays_due_after_a_converged_receipt(tmp_path, monkeypatch):
    from agentclaw.community.core.skill_center.services import capability_delivery_guard
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )

    monkeypatch.setattr(capability_delivery_guard, "_WAIT_SECONDS", 0)
    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    address = {"bot_id": "bot", "entity_id": "owner"}
    repository = CapabilityProjectionRepository(db)
    projector = _Projector([RuntimeProjectionResult.converged()] * 2)
    cache = MemoryCachePlugin()
    holder = CapabilityDeliveryGuard(cache)
    contender = CapabilityProjectionReconciler(
        repository=repository, projector=projector, guard=CapabilityDeliveryGuard(cache),
        bots=BotRepository(db), defer_enabled=False,
    )
    revision, result = await contender.finish_apply(**address, initialize=True)
    assert result.status is RuntimeProjectionStatus.CONVERGED

    async def legacy():
        pytest.fail("Lifecycle replay bypassed the existing ledger")

    async with holder.hold(**address):
        busy = await contender.project_competing_change(
            **address, owner_id="owner", scope=ProjectionScope.everything(),
            retired_mappings=(), legacy=legacy,
        )
        assert any(issue.code == "CAPABILITY_DELIVERY_BUSY" for issue in busy.issues)
        assert len(projector.scopes) == 1
        state = repository.read(**address)
        assert state.revision != revision
        assert state.retry_pending

    assert (await contender.reconcile(**address)).status is RuntimeProjectionStatus.CONVERGED
    assert projector.scopes == [ProjectionScope.everything()] * 2


@pytest.mark.asyncio
async def test_cutover_mcp_receipt_preserves_pending_skills_and_shared_fence(tmp_path):
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )
    from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
        RecoveringBotRuntimeProjector,
    )

    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    address = {"bot_id": "bot", "entity_id": "owner"}
    repository = CapabilityProjectionRepository(db)
    repository.require(**address, scope=ProjectionScope.everything())

    class CutoverProjector(_Projector):
        async def apply_mcp_and_cli_plan(self, *, scope, **kwargs):
            self.scopes.append(scope)
            assert repository.read(**address).write_blocked
            return RuntimeProjectionResult.converged(
                components={"mcp": RuntimeProjectionStatus.CONVERGED}
            )

    delegate = CutoverProjector([])
    reconciler = CapabilityProjectionReconciler(
        repository=repository, projector=delegate, guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=BotRepository(db), defer_enabled=True,
    )
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate, recovery=None, capability_projection=reconciler,
    )
    result = await projector.project_mcp_and_cli(
        bot_id="bot", owner_id="owner", scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )
    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert delegate.scopes == [ProjectionScope(mcp=True, claim_all_mcp=True)]
    state = repository.read(**address)
    assert state.scope == ProjectionScope(skills=True)
    assert state.retry_pending
    assert not state.write_blocked


@pytest.mark.asyncio
async def test_cutover_resolves_current_owner_and_keeps_preflight_failure_retryable(tmp_path):
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )
    from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
        RecoveringBotRuntimeProjector,
    )

    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    address = {"bot_id": "bot", "entity_id": "owner"}
    with db.transactional_orm_session() as session:
        row = session.query(BotModel).filter_by(**address).one()
        row.owner_id = "current-owner"
    repository = CapabilityProjectionRepository(db)
    repository.require(**address, scope=ProjectionScope.everything())
    prepared = []

    class CutoverProjector(_Projector):
        def resolve_plan(self, *, bot_id, owner_id, scope):
            prepared.append((bot_id, owner_id, scope))
            assert not repository.read(**address).write_blocked
            raise RuntimeError("configuration read temporarily unavailable")

        async def project_mcp_and_cli(self, *, bot_id, owner_id, scope):
            self.resolve_plan(bot_id=bot_id, owner_id=owner_id, scope=scope)

    delegate = CutoverProjector([])
    reconciler = CapabilityProjectionReconciler(
        repository=repository, projector=delegate, guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=BotRepository(db), defer_enabled=True,
    )
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate, recovery=None, capability_projection=reconciler,
    )
    scope = ProjectionScope(mcp=True, claim_all_mcp=True)
    result = await projector.project_mcp_and_cli(bot_id="bot", owner_id="owner", scope=scope)
    assert any(issue.code == "CAPABILITY_PROJECTION_PLAN_UNAVAILABLE" for issue in result.issues)
    assert prepared == [("bot", "current-owner", scope)]
    state = repository.read(**address)
    assert state.scope == ProjectionScope.everything()
    assert state.retry_pending
    assert not state.write_blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("device_succeeds", [True, False])
async def test_passport_failure_retries_its_obligation_in_the_same_revision(tmp_path, device_succeeds):
    from agentclaw.community.core.skill_center.errors import ArtifactProjectionNotDispatchedError
    from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
        CapabilityProjectionReconciler,
    )
    from tests.community.core.skill_center._test_skill_set_management_service_support import (
        _RuntimeFactory, _RuntimePassport, _TeclawRuntimeBots, _teclaw_runtime,
    )

    address = {"bot_id": "bot-1", "entity_id": "entity-1"}
    db = _Database(f"sqlite:///{tmp_path / 'state.db'}")
    with db.transactional_orm_session() as session:
        session.add(BotModel(
            **address, owner_id="true-owner", creator_id="true-owner", entity_type="staff",
            active_engine="teclaw", env="dev", status="ACTIVE",
        ))
    repository = CapabilityProjectionRepository(db)

    class Passport(_RuntimePassport):
        def update_passport(self, **kwargs):
            super().update_passport(**kwargs)
            if len(self.calls) == 1:
                raise RuntimeError("temporary Passport failure")

    class Bots(_TeclawRuntimeBots):
        def get_by_id_and_owner(self, bot_id, owner_id):
            return {**super().get_by_id_and_owner(bot_id, owner_id), "status": "ACTIVE"}

    factory = _RuntimeFactory()
    original = factory.service.project_whole_artifact
    device_attempts = []

    async def deliver(**kwargs):
        device_attempts.append(kwargs)
        if not device_succeeds and len(device_attempts) == 1:
            raise ArtifactProjectionNotDispatchedError()
        return await original(**kwargs)

    factory.service.project_whole_artifact = deliver
    passport = Passport()
    projector = _teclaw_runtime(factory, passport=passport)
    projector._bot_repo = Bots()
    reconciler = CapabilityProjectionReconciler(
        repository=repository, projector=projector, guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=BotRepository(db), defer_enabled=True,
    )

    revision, result = await reconciler.finish_apply(**address, initialize=True)
    assert result.status is RuntimeProjectionStatus.PENDING
    codes = {issue.code for issue in result.issues}
    assert "PASSPORT_RUNTIME_UNAVAILABLE" in codes
    assert ("ARTIFACT_PROJECTION_NOT_DISPATCHED" in codes) is not device_succeeds
    state = repository.read(**address)
    assert state.scope.passport
    assert state.scope.mcp is not device_succeeds
    assert state.retry_pending
    assert not state.write_blocked

    assert (await reconciler.reconcile(**address)).status is RuntimeProjectionStatus.CONVERGED
    assert len(device_attempts) == (1 if device_succeeds else 2)
    assert len(passport.calls) == 2
    state = repository.read(**address)
    assert state.revision == revision
    assert state.scope == ProjectionScope()
    assert not state.retry_pending
