"""Apply closes committed capability changes under the shared delivery lease."""

import asyncio
from types import SimpleNamespace

import pytest

from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.devices.services.device_context import expected_runtime_binding
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository
from agentclaw.community.core.skill_center.capability_projection_intent import capability_projection_intent
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.capability_delivery_guard import CapabilityDeliveryGuard
from agentclaw.community.core.skill_center.services.capability_projection_reconciler import CapabilityProjectionReconciler
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.cache import MemoryCachePlugin
from tests.community.repository.skill_center.capability_desired_state_support import _Database


ADDRESS = {"bot_id": "bot", "entity_id": "entity"}
SKILL = PoolSkillMapping(corpus="repo", relative_path="skills/old", link_name="retired")


class _Device:
    def __init__(self):
        self.id = 4
        self.device_id = "device"
        self.props = {}
        self.bot = {"binding_id": self.id, "owner_id": "owner", "status": "ACTIVE"}

    def get_by_id_and_entity(self, *_args):
        return {**self.bot, "binding_id": self.id}

    def get_by_id(self, binding_id):
        assert binding_id == self.id
        return SimpleNamespace(id=self.id, device_id=self.device_id, device_props=self.props)

    def get_active_by_bot_and_owner(self, *_args):
        return self.get_by_id(self.id)

    def update_device_props(self, *, binding_id, props):
        assert binding_id == self.id
        self.props.update(props)


class _Projector:
    def __init__(self, *results):
        self.results = iter(results)
        self.previous = (SKILL,)
        self.supplies = ()
        self.calls = []

    async def snapshot_skill_mappings(self, **_kwargs):
        return self.previous

    def resolve_plan(self, **_kwargs):
        return SimpleNamespace(bot={"status": "ACTIVE"}, projection=SimpleNamespace(skill_mappings=self.supplies))

    async def apply_plan(self, *, plan, retired_mappings, scope):
        binding = expected_runtime_binding("bot", "entity")
        self.calls.append((scope, retired_mappings, binding.device_id))
        return next(self.results)


def _reconciler(projector, device):
    return CapabilityProjectionReconciler(
        projector=projector, guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
        bots=device, bindings=device, defer_enabled=True,
    )


@pytest.mark.asyncio
async def test_aborted_apply_closes_only_committed_domains_once():
    projector = _Projector(RuntimeProjectionResult.converged())
    reconciler = _reconciler(projector, _Device())
    async with reconciler.deferred_apply(**ADDRESS, retry=None) as apply:
        with apply.write_scope():
            intent = capability_projection_intent(**ADDRESS)
            assert intent is not None
            intent.record(ProjectionScope(skills=True))
            intent.record(ProjectionScope(mcp=True, claimed_mcp=frozenset({"weather"})))
        receipt, retry = await apply.finish(initialize=False)
    assert receipt.status is RuntimeProjectionStatus.CONVERGED
    assert retry is None
    assert projector.calls == [
        (ProjectionScope(skills=True, mcp=True, claimed_mcp=frozenset({"weather"})), [SKILL], "device"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["same-device", "supplied-again", "new-device"])
async def test_unchanged_apply_retries_only_safe_retirements(case):
    projector = _Projector(
        RuntimeProjectionResult.pending(code="LOST_RECEIPT", reason="Request timed out", delivery_uncertain=True),
        RuntimeProjectionResult.converged(),
    )
    device = _Device()
    reconciler = _reconciler(projector, device)
    async with reconciler.deferred_apply(**ADDRESS, retry=None) as apply:
        with apply.write_scope():
            intent = capability_projection_intent(**ADDRESS)
            assert intent is not None
            intent.record(ProjectionScope(skills=True, mcp=True, released_mcp=frozenset({"old-mcp"})))
        _, retry = await apply.finish(initialize=True)
    assert retry.device_id == "device"
    assert retry.retired_mappings == (SKILL,)

    device.id += 1
    projector.previous = ()
    if case == "supplied-again":
        projector.supplies = (SKILL,)
    if case == "new-device":
        device.device_id = "replacement"
    async with reconciler.deferred_apply(**ADDRESS, retry=retry) as apply:
        receipt, retry = await apply.finish(initialize=True)
    assert receipt.status is RuntimeProjectionStatus.CONVERGED
    assert retry is None
    scope, retirements, physical_device = projector.calls[-1]
    assert scope.claim_all_mcp and scope.skills
    assert scope.released_mcp == ({"old-mcp"} if case != "new-device" else set())
    assert retirements == ([SKILL] if case == "same-device" else [])
    assert physical_device == device.device_id


@pytest.mark.asyncio
async def test_cancellation_keeps_the_lease_until_the_active_delivery_returns():
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingProjector(_Projector):
        async def apply_plan(self, **kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return await super().apply_plan(**kwargs)

    projector = BlockingProjector(
        RuntimeProjectionResult.converged(), RuntimeProjectionResult.converged(),
    )
    reconciler = _reconciler(projector, _Device())

    async def first_apply():
        async with reconciler.deferred_apply(**ADDRESS, retry=None) as apply:
            await apply.finish(initialize=True)

    first = asyncio.create_task(first_apply())
    await entered.wait()
    first.cancel()
    async def legacy():
        pytest.fail("Deferred Apply must share the active delivery lease")

    standalone = asyncio.create_task(reconciler.project_competing_change(
        **ADDRESS, owner_id="owner", scope=ProjectionScope(skills=True), retired_mappings=(), legacy=legacy,
    ))
    await asyncio.sleep(0.06)
    assert not standalone.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await standalone).status is RuntimeProjectionStatus.CONVERGED
    assert len(projector.calls) == 2


@pytest.mark.asyncio
async def test_failed_replacement_preserves_old_mapping_until_skill_delivery_converges():
    replacement = PoolSkillMapping(corpus="local", relative_path="replacement/skill", link_name=SKILL.link_name)
    old = PoolSkillMapping(corpus="local", relative_path="previous/skill", link_name=SKILL.link_name)
    failed = RuntimeProjectionResult.pending(code="MAPPING_WRITE_FAILED", reason="Could not apply mapping")
    mismatch = RuntimeProjectionResult(
        status=RuntimeProjectionStatus.DEGRADED,
        issues=(RuntimeProjectionIssue(
            resource_type="SKILL", name=old.link_name, requested_action="RETIRE",
            code="RETIRED_TARGET_IDENTITY_MISMATCH", reason="The previous link is still in use",
            status=RuntimeProjectionStatus.DEGRADED, retryable=False,
        ),),
    )
    projector = _Projector(failed, mismatch, RuntimeProjectionResult.converged())
    projector.previous = (old,)
    projector.supplies = (replacement,)
    reconciler = _reconciler(projector, _Device())

    async def apply_change(retry):
        async with reconciler.deferred_apply(**ADDRESS, retry=retry) as apply:
            with apply.write_scope():
                intent = capability_projection_intent(**ADDRESS)
                assert intent is not None
                intent.record(ProjectionScope(skills=True))
            return await apply.finish(initialize=True)

    _, retry = await apply_change(None)
    assert projector.calls[-1][1] == []
    assert retry.retired_mappings == (old,)

    projector.previous = (replacement,)
    projector.supplies = ()
    _, retry = await apply_change(retry)
    assert projector.calls[-1][1] == [replacement]
    assert retry.retired_mappings == (old,)

    projector.previous = ()
    result, retry = await apply_change(retry)
    assert projector.calls[-1][1] == [old]
    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert retry is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_with_apply", [False, True])
async def test_standalone_retirement_survives_failed_delivery(tmp_path, retry_with_apply):
    db = _Database(f"sqlite:///{tmp_path / 'standalone.db'}")
    with db.transactional_orm_session() as session:
        session.add(BotModel(
            bot_id="bot", entity_id="entity", owner_id="owner", creator_id="owner", env="dev",
            entity_type="staff", status="ACTIVE", binding_id=4,
        ))
        session.add(EntityDeviceBinding(
            id=4, entity_id="entity", entity_type="staff", device_id="device", device_provider="arca",
            env="dev", status="ACTIVE", applied_by="owner", device_props='{"adapter_port":20003}',
        ))
    bots, bindings = BotRepository(db), DeviceRepository(db)

    def new_worker(projector):
        return CapabilityProjectionReconciler(
            projector=projector, guard=CapabilityDeliveryGuard(MemoryCachePlugin()),
            bots=bots, bindings=bindings, defer_enabled=True,
        )
    local = PoolSkillMapping(corpus="local", relative_path="local/removed", link_name="removed")
    failed = _Projector(RuntimeProjectionResult.pending(code="FAILED", reason="Device unavailable"))
    failed_reconciler = new_worker(failed)

    async def unused_legacy():
        pytest.fail("The current Bot is using combined capability delivery")

    result = await failed_reconciler.project_competing_change(
        **ADDRESS, owner_id="owner", scope=ProjectionScope(skills=True),
        retired_mappings=(local,), legacy=unused_legacy,
    )
    assert result.status is RuntimeProjectionStatus.PENDING
    assert failed.calls[0][1] == [local]

    stored = bindings.get_by_id(4)
    assert stored.to_dict()["device_props"] == {"adapter_port": 20003}

    restarted = _Projector(RuntimeProjectionResult.converged(), RuntimeProjectionResult.converged())
    restarted.previous = ()
    new_reconciler = new_worker(restarted)
    if retry_with_apply:
        async with new_reconciler.deferred_apply(**ADDRESS, retry=None) as apply:
            result, _ = await apply.finish(initialize=True)
    else:
        result = await new_reconciler.project_competing_change(
            **ADDRESS, owner_id="owner", scope=ProjectionScope(skills=True),
            retired_mappings=(), legacy=unused_legacy,
        )
    assert result.status is RuntimeProjectionStatus.CONVERGED
    assert restarted.calls[0][1] == [local]
    after = bindings.get_by_id(4).device_props
    assert after["adapter_port"] == 20003
    assert all(after[key] is None for key in after if key != "adapter_port")
