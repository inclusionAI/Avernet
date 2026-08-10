"""L0 — full teclaw publish lifecycle through the production wiring.

Validates the cross-publish-boundary harness itself: endpoints → durable tasks
(worker-drained) → real BaasService against the stateful LocalBaas → the record
lands SUCCESS with the ledger, bindings, and liveness predicate all consistent.
Every later scenario builds on this shape.
"""
import pytest

from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationState,
    PublishStatus,
)

from tests.community.e2e.publish_boundary.harness import (
    PROCESS,
    V1,
    api,
    drain,
    ext_of,
    flow,
    install_engine,
    install_local_baas,
    ledger,
    seed_draft,
    status_of,
)

pytestmark = pytest.mark.integration


async def run_v1_to_success(app, world, baas):
    """Drive the seeded DRAFT record V1 to SUCCESS. Returns the online bot uuid."""
    # DRAFT → BUILDING (synchronous, user-driven), then the durable verify_flow
    # builds (teclaw compose+freeze) and issues the verify create.
    resp = await api(app, "POST", PROCESS, json={"publish_id": V1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "building"

    await drain(world, until=lambda: status_of(world, V1) == PublishStatus.VALIDATE_PUB.value)
    assert status_of(world, V1) == PublishStatus.VALIDATE_PUB.value
    assert baas.bot_count() == 1  # the verify bot, workflow still ACTIVE

    # BaaS finishes the verify deploy → the poll advances to VALIDATING.
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V1) == PublishStatus.VALIDATING.value

    # Go-live: VALIDATING → ONLINE_PUB (synchronous), online_release issues the
    # online first release (a second bot — verify and online are separate).
    resp = await api(app, "POST", PROCESS, json={"publish_id": V1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "online_pub"

    await drain(world, until=lambda: (ext_of(world, V1).get("publish") or {}).get("online"))
    assert baas.bot_count() == 2

    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V1) == PublishStatus.SUCCESS.value

    online_binding_id = ext_of(world, V1)["binding"]["online"]
    binding = world.get(DeviceBindingRepository).get_by_id(online_binding_id)
    assert binding.status == "ACTIVE"
    return binding.device_id


@pytest.mark.asyncio
async def test_l0_full_lifecycle_draft_to_success(app_with_testing_modules, world):
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)

    online_uuid = await run_v1_to_success(app_with_testing_modules, world, baas)

    # Ledger: one completed FIRST_RELEASE per stage, each carrying its bot +
    # workflow; exactly two creates ever reached BaaS (verify + online).
    verify_op = ledger(world).get_latest_by_kind(V1, "first_release", "verify")
    online_op = ledger(world).get_latest_by_kind(V1, "first_release", "online")
    assert verify_op.state == PublishOperationState.COMPLETED.value
    assert online_op.state == PublishOperationState.COMPLETED.value
    assert online_op.bot_uuid == online_uuid
    assert baas.creates() == ["BOT-1", "BOT-2"]
    assert len(baas.workflows_of(online_uuid)) == 1

    # The record's online release is the current live deployment.
    assert flow(world).is_current_online_deployment(V1) is True
