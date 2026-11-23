"""Apply closes committed capability changes under the shared delivery lease."""

import asyncio
from types import SimpleNamespace

import pytest

from agentclaw.community.core.devices.services.device_context import expected_runtime_binding
from agentclaw.community.core.skill_center.capability_projection_intent import capability_projection_intent
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.capability_delivery_guard import CapabilityDeliveryGuard
from agentclaw.community.core.skill_center.services.capability_projection_reconciler import CapabilityProjectionReconciler
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.plugins.local.cache import MemoryCachePlugin


ADDRESS = {"bot_id": "bot", "entity_id": "entity"}
SKILL = PoolSkillMapping(corpus="repo", relative_path="skills/old", link_name="retired")


class _Device:
    def __init__(self):
        self.id = 4
        self.device_id = "device"
        self.bot = {"binding_id": self.id, "owner_id": "owner", "status": "ACTIVE"}

    def get_by_id_and_entity(self, *_args):
        return {**self.bot, "binding_id": self.id}

    def get_active_by_bot_and_owner(self, *_args):
        return SimpleNamespace(id=self.id, device_id=self.device_id)


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
