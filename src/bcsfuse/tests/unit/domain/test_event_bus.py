"""EventBus + WorkerProfileCreatedEvent 单元测试。"""

from __future__ import annotations

import asyncio
import pytest

from src.domain.events import get_event_bus, reset_event_bus, WorkerProfileCreatedEvent


@pytest.fixture(autouse=True)
def _reset_event_bus():
    yield
    reset_event_bus()


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_calls_handler(self) -> None:
        bus = get_event_bus()
        received: list[WorkerProfileCreatedEvent] = []

        async def handler(event: WorkerProfileCreatedEvent) -> None:
            received.append(event)

        bus.subscribe(WorkerProfileCreatedEvent, handler)
        bus.publish(WorkerProfileCreatedEvent(worker_id="w1"))

        # Allow async task to run
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].worker_id == "w1"

    @pytest.mark.asyncio
    async def test_multiple_handlers(self) -> None:
        bus = get_event_bus()
        count1 = 0
        count2 = 0

        async def h1(event: WorkerProfileCreatedEvent) -> None:
            nonlocal count1
            count1 += 1

        async def h2(event: WorkerProfileCreatedEvent) -> None:
            nonlocal count2
            count2 += 1

        bus.subscribe(WorkerProfileCreatedEvent, h1)
        bus.subscribe(WorkerProfileCreatedEvent, h2)
        bus.publish(WorkerProfileCreatedEvent(worker_id="w1"))

        await asyncio.sleep(0.1)
        assert count1 == 1
        assert count2 == 1

    @pytest.mark.asyncio
    async def test_no_handler_no_error(self) -> None:
        bus = get_event_bus()
        bus.publish(WorkerProfileCreatedEvent(worker_id="w1"))
        await asyncio.sleep(0.1)

    def test_reset_creates_new_bus(self) -> None:
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()
        assert bus1 is not bus2


class TestWorkerProfileCreatedEvent:
    def test_event_fields(self) -> None:
        event = WorkerProfileCreatedEvent(worker_id="bot-123")
        assert event.worker_id == "bot-123"