"""Plugin v1 consumer conformance through the production DI world seam."""

import asyncio
from dataclasses import replace

import pytest

from agentclaw.community.core.repository.protocols.platform import (
    SessionResourceRepositoryProtocol,
    ResourceWithdrawalRepositoryProtocol,
)
from agentclaw.community.core.session_resources.withdrawal_worker import (
    ResourceWithdrawalWorker,
)
from agentclaw.community.di.tc_resource_withdrawal_config import (
    ResourceWithdrawalConfig,
)
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalPublisherPlugin,
    WithdrawalDeliveryError,
)
from tests.community.core.session_resources.test_repository import _record


@pytest.mark.parametrize("fails", [False, True])
def test_worker_consumes_local_publisher_via_world(world, fails):
    resources = world.get(SessionResourceRepositoryProtocol)
    outbox = world.get(ResourceWithdrawalRepositoryProtocol)
    publisher = world.get(ResourceWithdrawalPublisherPlugin)
    resources.create(
        replace(_record(), tenant=world.get(ResourceWithdrawalConfig).tenant)
    )
    resources.soft_delete(
        "sr_001",
        "owner-1",
        "bot-1",
        "session-hash",
        withdrawal_scope_types=("personal_bot_chat",),
    )
    if fails:

        def fail(_):
            raise WithdrawalDeliveryError("http_403", retryable=False)

        publisher.set_override("publish", fail)
    worker = world.get(ResourceWithdrawalWorker)
    asyncio.run(worker.run_once())
    fact = outbox.get("tc.resource.withdrawn:sr_001")
    assert fact.status == ("blocked" if fails else "accepted")
    calls = publisher.calls_to("publish")
    assert len(calls) == 1
    assert calls[0].args[0].as_payload() == {
        "event_id": fact.event_id,
        "res_id": "sr_001",
    }


@pytest.mark.parametrize(
    "event_id,res_id", [("tc.resource.withdrawn:bad/id", "bad/id"), ("other", "sr_1")]
)
def test_event_identity_contract_rejects_invalid_input(event_id, res_id):
    from agentclaw.community.plugin_api.tc_resource_withdrawal import (
        ResourceWithdrawalEvent,
    )

    with pytest.raises(ValueError):
        ResourceWithdrawalEvent(event_id=event_id, res_id=res_id)


def test_receipt_contract_rejects_unknown_status():
    from agentclaw.community.plugin_api.tc_resource_withdrawal import WithdrawalReceipt

    with pytest.raises(ValueError):
        WithdrawalReceipt(event_id="tc.resource.withdrawn:sr_1", status="unknown")
