"""Consumer conformance for the Bot runtime projection Service API."""

from __future__ import annotations

import pytest
from injector import inject, singleton

from agentclaw.community.api.bot_runtime_projection_reconciler import (
    BotRuntimeProjectionReconcilerProtocol,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projection_reconciler import (
    BotRuntimeProjectionReconciler,
)


class _RecordingReconciler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def reconcile(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class _Consumer:
    @inject
    def __init__(self, runtime: BotRuntimeProjectionReconcilerProtocol) -> None:
        self._runtime = runtime

    async def refresh(self) -> None:
        await self._runtime.reconcile(bot_id="bot-1", owner_id="owner-1")


def _consumer(world, runtime: _RecordingReconciler) -> _Consumer:
    world.injector.binder.bind(
        BotRuntimeProjectionReconcilerProtocol,
        to=runtime,
        scope=singleton,
    )
    return world.injector.create_object(_Consumer)


def test_world_wires_service_api_to_the_real_reconciler(world) -> None:
    assert isinstance(
        world.get(BotRuntimeProjectionReconcilerProtocol),
        BotRuntimeProjectionReconciler,
    )


@pytest.mark.asyncio
async def test_reconcile_service_api_success_reaches_the_bound_implementation(
    world,
) -> None:
    runtime = _RecordingReconciler()
    consumer = _consumer(world, runtime)

    await consumer.refresh()

    assert runtime.calls == [{"bot_id": "bot-1", "owner_id": "owner-1"}]


@pytest.mark.asyncio
async def test_reconcile_service_api_propagates_failure_after_actual_invocation(
    world,
) -> None:
    runtime = _RecordingReconciler(error=RuntimeError("runtime unavailable"))
    consumer = _consumer(world, runtime)

    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await consumer.refresh()

    assert runtime.calls == [{"bot_id": "bot-1", "owner_id": "owner-1"}]
