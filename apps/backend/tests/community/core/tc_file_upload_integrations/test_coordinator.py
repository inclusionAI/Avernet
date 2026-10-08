from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agentclaw.community.plugin_api.tc_resource_ready import TcResourceReadyEvent
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)


def _resource(
    resource_id: str = "sr_001",
    status: SessionResourceStatus = SessionResourceStatus.READY,
) -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id=resource_id,
        owner_id="user-1",
        bot_id="bot-1",
        scope_type="session",
        scope_key_hash="scope-hash",
        session_key_hash="session-hash",
        engine_type="claude_code",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        display_name="report.pdf",
        filename="report.pdf",
        device_path="workspace/report.pdf",
        workspace_relative_path="report.pdf",
        transfer_id="transfer-internal-1",
        status=status,
        size_bytes=123,
    )


class _Publisher:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls: list[TcResourceReadyEvent] = []
        self.failure = failure
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def publish(self, event: TcResourceReadyEvent) -> None:
        self.calls.append(event)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure


async def _settle(coordinator: TcResourceReadyCoordinator) -> None:
    while coordinator._tasks:
        await asyncio.gather(*tuple(coordinator._tasks), return_exceptions=True)


def _coordinator(publisher: _Publisher, **kwargs) -> TcResourceReadyCoordinator:
    return TcResourceReadyCoordinator(publisher=publisher, **kwargs)


@pytest.mark.asyncio
async def test_non_ready_observation_does_not_publish():
    publisher = _Publisher()
    coordinator = _coordinator(publisher)

    coordinator.notify_in_background(
        replace(_resource(), status=SessionResourceStatus.DEVICE_SYNCING)
    )
    await asyncio.sleep(0)

    assert publisher.calls == []
    assert coordinator._tasks == set()


@pytest.mark.asyncio
async def test_ready_observation_publishes_the_stable_three_field_event_once():
    publisher = _Publisher()
    coordinator = _coordinator(publisher)

    for _ in range(10):
        coordinator.notify_in_background(_resource())
    await _settle(coordinator)

    assert [event.as_payload() for event in publisher.calls] == [
        {
            "schema_version": "1",
            "event_id": "tc.resource.ready:sr_001",
            "res_id": "sr_001",
        }
    ]


@pytest.mark.asyncio
async def test_publisher_failure_is_contained_and_remains_retryable():
    publisher = _Publisher(failure=RuntimeError("sensitive downstream detail"))
    coordinator = _coordinator(publisher)

    coordinator.notify_in_background(_resource())
    await _settle(coordinator)
    coordinator.notify_in_background(_resource())
    await _settle(coordinator)

    assert len(publisher.calls) == 2


@pytest.mark.asyncio
async def test_in_flight_cap_drops_overload_without_tombstoning_it():
    publisher = _Publisher()
    publisher.release = asyncio.Event()
    coordinator = _coordinator(publisher, max_in_flight=1)

    coordinator.notify_in_background(_resource("sr_001"))
    await publisher.started.wait()
    coordinator.notify_in_background(_resource("sr_002"))

    assert [event.res_id for event in publisher.calls] == ["sr_001"]
    assert "sr_002" not in coordinator._recent

    publisher.release.set()
    await _settle(coordinator)
    coordinator.notify_in_background(_resource("sr_002"))
    await _settle(coordinator)

    assert [event.res_id for event in publisher.calls] == ["sr_001", "sr_002"]


@pytest.mark.asyncio
async def test_dedupe_entry_expires_and_allows_a_later_observation():
    now = [100.0]
    publisher = _Publisher()
    coordinator = _coordinator(
        publisher,
        dedupe_ttl_seconds=10.0,
        monotonic=lambda: now[0],
    )

    coordinator.notify_in_background(_resource())
    await _settle(coordinator)
    now[0] = 109.0
    coordinator.notify_in_background(_resource())
    await _settle(coordinator)
    assert len(publisher.calls) == 1

    now[0] = 110.0
    coordinator.notify_in_background(_resource())
    await _settle(coordinator)
    assert len(publisher.calls) == 2


@pytest.mark.asyncio
async def test_dedupe_cache_is_capacity_bounded_and_evicts_lru():
    publisher = _Publisher()
    coordinator = _coordinator(publisher, dedupe_max_entries=1)

    coordinator.notify_in_background(_resource("sr_001"))
    await _settle(coordinator)
    coordinator.notify_in_background(_resource("sr_002"))
    await _settle(coordinator)

    assert list(coordinator._recent) == ["sr_002"]

    coordinator.notify_in_background(_resource("sr_001"))
    await _settle(coordinator)
    assert [event.res_id for event in publisher.calls] == [
        "sr_001",
        "sr_002",
        "sr_001",
    ]


@pytest.mark.asyncio
async def test_task_creation_failure_releases_the_reservation(monkeypatch):
    publisher = _Publisher()
    coordinator = _coordinator(publisher)

    def fail_to_schedule(coro):
        raise RuntimeError("event loop unavailable")

    monkeypatch.setattr(
        "agentclaw.community.core.tc_file_upload_integrations.coordinator.asyncio.create_task",
        fail_to_schedule,
    )

    coordinator.notify_in_background(_resource())

    assert publisher.calls == []
    assert coordinator._in_flight == set()
    assert coordinator._tasks == set()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_in_flight": 0}, "max_in_flight_must_be_positive"),
        ({"dedupe_ttl_seconds": 0}, "dedupe_ttl_seconds_must_be_positive"),
        ({"dedupe_max_entries": 0}, "dedupe_max_entries_must_be_positive"),
    ],
)
def test_coordinator_rejects_unbounded_configuration(kwargs, error):
    with pytest.raises(ValueError, match=error):
        _coordinator(_Publisher(), **kwargs)
