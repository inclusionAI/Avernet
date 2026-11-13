"""Durable event delivery acknowledges only successful plugin output."""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.release_audit_delivery import (
    ReleaseAuditDeliveryHandler, ReleaseAuditDeliveryLifecycle,
)
from agentclaw.community.core.task_queue.types import Complete


def test_handler_delivers_specific_event():
    delivery = MagicMock()
    handler = ReleaseAuditDeliveryHandler(delivery)
    assert isinstance(handler.handle({"event_id": "change:2"}), Complete)
    delivery.deliver.assert_called_once_with("change:2")


def test_handler_failure_is_left_to_persistent_queue_retry():
    delivery = MagicMock()
    delivery.deliver.side_effect = RuntimeError("sink unavailable")
    with pytest.raises(RuntimeError, match="sink unavailable"):
        ReleaseAuditDeliveryHandler(delivery).handle({"event_id": "change:2"})


@pytest.mark.asyncio
async def test_lifecycle_scans_committed_events_and_registers_handler():
    registry, queue, delivery = MagicMock(), MagicMock(), MagicMock()
    delivery.pending_event_ids.return_value = ["c:1", "c:2"]
    lifecycle = ReleaseAuditDeliveryLifecycle(registry=registry, queue=queue, delivery=delivery)
    await lifecycle.bootstrap()
    lifecycle.enqueue_pending()
    assert registry.register.call_count == 1
    assert queue.enqueue.call_count == 2
    assert queue.enqueue.call_args.kwargs["idempotency_key"] == "release-audit:c:2"


@pytest.mark.parametrize("payload", [None, {}, {"event_id": 1}])
def test_handler_rejects_invalid_payload(payload):
    with pytest.raises(ValueError, match="event_id required"):
        ReleaseAuditDeliveryHandler(MagicMock()).handle(payload)


@pytest.mark.asyncio
async def test_lifecycle_startup_recovers_work_and_shutdown_cancels_scan():
    import asyncio
    registry, queue, delivery = MagicMock(), MagicMock(), MagicMock()
    loop = asyncio.get_running_loop()
    scanned = asyncio.Event()

    def pending():
        loop.call_soon_threadsafe(scanned.set)
        return ["recovered:2"]

    delivery.pending_event_ids.side_effect = pending
    lifecycle = ReleaseAuditDeliveryLifecycle(registry=registry, queue=queue, delivery=delivery)
    await lifecycle.startup()
    await asyncio.wait_for(scanned.wait(), timeout=2)
    # scanner's enqueue occurs in the same thread before to_thread returns
    await lifecycle.shutdown()
    assert lifecycle._task.done()
    queue.enqueue.assert_called_once()
    await ReleaseAuditDeliveryLifecycle(registry=registry, queue=queue, delivery=delivery).shutdown()


@pytest.mark.asyncio
async def test_scanner_failure_is_logged_and_waits_for_next_scan(monkeypatch, caplog):
    import asyncio
    delivery = MagicMock()
    delivery.pending_event_ids.side_effect = RuntimeError("private-auth-material")
    lifecycle = ReleaseAuditDeliveryLifecycle(registry=MagicMock(), queue=MagicMock(), delivery=delivery)
    delays = []

    async def stop_after_wait(delay):
        delays.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_wait)
    with pytest.raises(asyncio.CancelledError):
        await lifecycle._scan()
    assert delays == [8]
    assert "release_audit.scan_failed" in caplog.text
    assert "private-auth-material" not in caplog.text
