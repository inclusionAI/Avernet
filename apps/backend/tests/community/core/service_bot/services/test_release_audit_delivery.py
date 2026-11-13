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
