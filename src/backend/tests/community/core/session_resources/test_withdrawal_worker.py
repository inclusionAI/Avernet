"""Consumer tests use the durable file store, never a fake outbox."""

import pytest

from agentclaw.community.core.session_resources.withdrawal_worker import (
    ResourceWithdrawalWorker,
)
from agentclaw.community.di.tc_resource_withdrawal_config import (
    ResourceWithdrawalConfig,
)
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    WithdrawalDeliveryError,
)
from agentclaw.community.plugins.local.tc_resource_withdrawal import (
    LocalResourceWithdrawalPublisher,
)
from tests.community.core.session_resources.test_withdrawal_repository import (
    delete,
)
from tests.community.core.session_resources.test_repository import _record


def worker(store, publisher=None, **settings):
    return ResourceWithdrawalWorker(
        store[2],
        publisher or LocalResourceWithdrawalPublisher(),
        ResourceWithdrawalConfig(tenant="tenant-1", **settings),
    )


@pytest.mark.asyncio
async def test_success_only_after_valid_durable_receipt(store):
    store[1].create(_record())
    delete(store[1])
    publisher = LocalResourceWithdrawalPublisher()
    assert await worker(store, publisher).run_once()
    assert store[2].get("tc.resource.withdrawn:sr_001").status == "accepted"
    assert len(publisher.calls_to("publish")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable,expected", [(True, "pending"), (False, "blocked")])
async def test_delivery_failure_is_durable_and_does_not_restore_resource(
    store, retryable, expected
):
    store[1].create(_record())
    delete(store[1])
    publisher = LocalResourceWithdrawalPublisher()

    def fail(_):
        raise WithdrawalDeliveryError(
            "http_503" if retryable else "http_403", retryable=retryable
        )

    publisher.set_override("publish", fail)
    await worker(store, publisher).run_once()
    fact = store[2].get("tc.resource.withdrawn:sr_001")
    assert fact.status == expected
    assert fact.last_error.startswith("http_")
    assert store[1].get_by_resource_id("sr_001").status.value == "deleted"
    assert not await worker(store, publisher).run_once()


@pytest.mark.asyncio
async def test_exhaustion_unknown_error_and_invalid_plugin_receipt_block(store):
    store[1].create(_record())
    delete(store[1])
    publisher = LocalResourceWithdrawalPublisher()
    publisher.set_response("publish", None)
    await worker(store, publisher).run_once()
    fact = store[2].get("tc.resource.withdrawn:sr_001")
    assert fact.status == "blocked"
    assert fact.last_error == "invalid_receipt"
    assert store[2].replay(
        event_id=fact.event_id,
        tenant="tenant-1",
        expected_attempts=1,
        actor="test-operator",
        reason="test recovery",
    )

    def fail(_):
        raise WithdrawalDeliveryError("timeout", retryable=True)

    publisher.set_override("publish", fail)
    await worker(store, publisher, max_attempts=1).run_once()
    assert store[2].get(fact.event_id).last_error == "retry_exhausted_timeout"


@pytest.mark.asyncio
async def test_paused_lifecycle_never_claims_and_shutdown_is_clean(store):
    store[1].create(_record())
    delete(store[1])
    instance = worker(store)
    await instance.startup()
    await instance.shutdown()
    assert store[2].get("tc.resource.withdrawn:sr_001").attempts == 0


@pytest.mark.asyncio
async def test_storage_failure_propagates_without_false_ack(store, monkeypatch):
    store[1].create(_record())
    delete(store[1])

    def fail(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(store[2], "finish", fail)
    with pytest.raises(RuntimeError, match="storage"):
        await worker(store).run_once()
    assert store[2].get("tc.resource.withdrawn:sr_001").status == "processing"


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_failure", [False, True])
async def test_active_lifecycle_recovers_storage_error_and_drains(
    store, monkeypatch, storage_failure
):
    import asyncio
    from unittest.mock import Mock

    store[1].create(_record())
    delete(store[1])
    stats = store[2].stats
    if storage_failure:
        monkeypatch.setattr(
            store[2],
            "stats",
            Mock(side_effect=[RuntimeError("private"), stats(tenant="tenant-1")]),
        )
    instance = worker(
        store,
        enabled=True,
        base_url="https://ecb.example.test",
        secret_name="test",
        poll_seconds=1,
    )
    await instance.startup()
    task = instance._task
    await instance.startup()
    assert instance._task is task
    try:
        async with asyncio.timeout(5):
            while store[2].get("tc.resource.withdrawn:sr_001").status != "accepted":
                await asyncio.sleep(0.01)
    finally:
        await instance.shutdown()
    assert task.done()
    assert instance._task is None


@pytest.mark.asyncio
async def test_lost_lease_is_not_acknowledged(store, monkeypatch, caplog):
    store[1].create(_record())
    delete(store[1])
    monkeypatch.setattr(store[2], "finish", lambda *a, **kw: False)
    await worker(store).run_once()
    assert store[2].get("tc.resource.withdrawn:sr_001").status == "processing"
    assert "tc.withdrawal.lease_lost" in caplog.text
