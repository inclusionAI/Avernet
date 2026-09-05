"""Consumer conformance for the Bot runtime projection Service API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from injector import inject, singleton

from agentclaw.community.api.bot_runtime_projector import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol as CoreBotRuntimeProjectorProtocol,
    ProjectionScope,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)


class _RecordingReconciler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def snapshot_skill_mappings(self, **kwargs):
        self.calls.append({"operation": "snapshot", **kwargs})
        if self.error is not None:
            raise self.error
        return ()

    async def project(self, **kwargs) -> None:
        self.calls.append({"operation": "full", **kwargs})
        if self.error is not None:
            raise self.error

    def resolve_plan(self, **kwargs):
        self.calls.append({"operation": "resolve_plan", **kwargs})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(projection=SimpleNamespace(skill_mappings=()))

    async def apply_plan(self, **kwargs) -> None:
        self.calls.append({"operation": "apply_plan", **kwargs})
        if self.error is not None:
            raise self.error

    async def project_mcp_and_cli(self, **kwargs) -> None:
        self.calls.append({"operation": "non_skill", **kwargs})
        if self.error is not None:
            raise self.error


class _Consumer:
    @inject
    def __init__(self, runtime: BotRuntimeProjectorProtocol) -> None:
        self._runtime = runtime

    async def refresh(self) -> None:
        await self._runtime.project(bot_id="bot-1", owner_id="owner-1")

    async def snapshot_skill_mappings(self) -> None:
        await self._runtime.snapshot_skill_mappings(bot_id="bot-1", owner_id="owner-1")

    async def resolve_and_apply(self) -> None:
        scope = ProjectionScope(skills=True)
        plan = self._runtime.resolve_plan(
            bot_id="bot-1",
            owner_id="owner-1",
            scope=scope,
        )
        await self._runtime.apply_plan(plan=plan, scope=scope)

    async def refresh_non_skill(self) -> None:
        await self._runtime.project_mcp_and_cli(
            bot_id="bot-1", owner_id="owner-1"
        )


def _consumer(world, runtime: _RecordingReconciler) -> _Consumer:
    world.injector.binder.bind(
        BotRuntimeProjectorProtocol,
        to=runtime,
        scope=singleton,
    )
    return world.injector.create_object(_Consumer)


def test_world_wires_service_api_to_the_real_reconciler(world) -> None:
    assert isinstance(
        world.get(BotRuntimeProjectorProtocol),
        BotRuntimeProjector,
    )
    assert world.get(BotRuntimeProjectorProtocol) is world.get(
        CoreBotRuntimeProjectorProtocol
    )


@pytest.mark.asyncio
async def test_reconcile_service_api_success_reaches_the_bound_implementation(
    world,
) -> None:
    runtime = _RecordingReconciler()
    consumer = _consumer(world, runtime)

    await consumer.refresh()

    assert runtime.calls == [
        {"operation": "full", "bot_id": "bot-1", "owner_id": "owner-1"}
    ]


@pytest.mark.asyncio
async def test_snapshot_service_api_reads_without_reconciliation(world) -> None:
    runtime = _RecordingReconciler()
    consumer = _consumer(world, runtime)

    await consumer.snapshot_skill_mappings()

    assert runtime.calls == [
        {"operation": "snapshot", "bot_id": "bot-1", "owner_id": "owner-1"}
    ]


@pytest.mark.asyncio
async def test_resolved_plan_service_api_applies_the_same_plan(world) -> None:
    runtime = _RecordingReconciler()
    consumer = _consumer(world, runtime)

    await consumer.resolve_and_apply()

    assert runtime.calls[0]["operation"] == "resolve_plan"
    assert runtime.calls[1]["operation"] == "apply_plan"
    assert runtime.calls[1]["plan"].projection.skill_mappings == ()


@pytest.mark.asyncio
async def test_non_skill_service_api_reaches_the_bound_implementation(world) -> None:
    runtime = _RecordingReconciler()
    consumer = _consumer(world, runtime)

    await consumer.refresh_non_skill()

    assert runtime.calls == [
        {"operation": "non_skill", "bot_id": "bot-1", "owner_id": "owner-1"}
    ]


@pytest.mark.asyncio
async def test_reconcile_service_api_propagates_failure_after_actual_invocation(
    world,
) -> None:
    runtime = _RecordingReconciler(error=RuntimeError("runtime unavailable"))
    consumer = _consumer(world, runtime)

    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await consumer.refresh()

    assert runtime.calls == [
        {"operation": "full", "bot_id": "bot-1", "owner_id": "owner-1"}
    ]
