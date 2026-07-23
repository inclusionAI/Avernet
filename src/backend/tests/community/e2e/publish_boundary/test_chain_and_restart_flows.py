"""C1-C5 — upgrade chains, rollback-then-re-promote, restart semantics, and
the recreate leg, end-to-end through the production wiring on the shared
online bot."""
import pytest

from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationState,
    PublishStatus,
)

from tests.community.e2e.publish_boundary.harness import (
    PROCESS,
    RESTART,
    ROLLBACK,
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
from tests.community.e2e.publish_boundary.test_lifecycle_baseline import (
    run_v1_to_success,
)
from tests.community.e2e.publish_boundary.test_retry_and_failure_flows import (
    RESTART_STATUS,
    V2,
    _upgrade_to_online_pub,
)

pytestmark = pytest.mark.integration


async def _chain_v2_success(app, world, baas) -> str:
    """v1 SUCCESS → v2 upgrade lands on the shared bot → v2 SUCCESS.
    Returns the shared online bot uuid."""
    shared = await _upgrade_to_online_pub(app, world, baas)
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.SUCCESS.value
    return shared


@pytest.mark.asyncio
async def test_c1_upgrade_chain(app_with_testing_modules, world):
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    await run_v1_to_success(app, world, baas)
    shared = await _chain_v2_success(app, world, baas)

    # v1 superseded (status + liveness), v2 current; the chain shared one
    # online bot (v2's online deploy is an UPDATE, never a create).
    assert status_of(world, V1) == PublishStatus.UPGRADED.value
    assert flow(world).is_current_online_deployment(V1) is False
    assert flow(world).is_current_online_deployment(V2) is True
    assert len(baas.creates()) == 2  # v1-verify + the shared online bot
    assert baas.updates_of(shared) == 1


@pytest.mark.asyncio
async def test_c2_rollback_then_repromote(app_with_testing_modules, world):
    """The #5984 shape, end-to-end: rollback demotes v2 and re-deploys v1's
    version onto the shared bot (ROLLBACK_DEPLOY, higher workflow id); the
    demoted record's old COMPLETED release is stale, so re-promoting v2 must
    RE-RUN its online release — a fresh deploy lands, the poll follows the new
    workflow, and nothing strands."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    await run_v1_to_success(app, world, baas)
    shared = await _chain_v2_success(app, world, baas)
    v2_first_online_wid = (ext_of(world, V2).get("publish") or {})["online"]

    # Rollback v2 → v2 DRAFT; v1 parked at ONLINE_PUB behind a ROLLBACK_DEPLOY
    # on the shared bot; the durable poll drives v1 back to SUCCESS.
    resp = await api(app, "POST", ROLLBACK.format(publish_id=V2))
    assert resp.json()["success"] is True, resp.text
    assert status_of(world, V2) == PublishStatus.DRAFT.value
    await drain(world, until=lambda: baas.updates_of(shared) == 2)
    rb = ledger(world).get_latest_by_kind(V1, "rollback_deploy", "online")
    assert rb is not None and rb.baas_publish_id is not None
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V1) == PublishStatus.SUCCESS.value
    # The demoted record's release is no longer the live deployment.
    assert flow(world).is_current_online_deployment(V2) is False

    # Re-promote v2 from DRAFT through the full flow. The online gate must
    # re-run the release (the #341 regression skipped it and stranded here).
    await api(app, "POST", PROCESS, json={"publish_id": V2})
    await drain(world, until=lambda: status_of(world, V2) == PublishStatus.VALIDATE_PUB.value)
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.VALIDATING.value

    await api(app, "POST", PROCESS, json={"publish_id": V2})
    await drain(world, until=lambda: baas.updates_of(shared) == 4)
    # A fresh online deploy was issued and ext points at the NEW workflow —
    # the stranded-poll symptom ("No baas_publish_id found") is dead.
    v2_second_online_wid = (ext_of(world, V2).get("publish") or {})["online"]
    assert v2_second_online_wid != v2_first_online_wid

    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.SUCCESS.value
    assert flow(world).is_current_online_deployment(V2) is True
    assert len(baas.creates()) == 2  # still no duplicate bot


@pytest.mark.asyncio
async def test_c3_restart_always_hits_baas_despite_current_deployment(
    app_with_testing_modules, world
):
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    online_uuid = await run_v1_to_success(app, world, baas)
    assert flow(world).is_current_online_deployment(V1) is True

    resp = await api(app, "POST", RESTART.format(publish_id=V1))
    assert resp.json()["success"] is True, resp.text
    await drain(world, until=lambda: baas.updates_of(online_uuid) == 1)
    # The skip-if-current check never applies to restart: BaaS was called.
    assert baas.updates_of(online_uuid) == 1
    assert baas.latest_workflow(online_uuid)["publish_type"] == "UPDATE"

    baas.finish_all("SUCCESS")
    await api(app, "POST", RESTART_STATUS.format(publish_id=V1))
    assert status_of(world, V1) == PublishStatus.SUCCESS.value
    assert flow(world).is_current_online_deployment(V1) is True


@pytest.mark.asyncio
async def test_c4_restart_recreate_on_gone_bot(app_with_testing_modules, world):
    """The bot is gone server-side: restart's recreate leg abandons the RESTART
    op, completes a FIRST_RELEASE op with a NEW bot + NEW binding, moves the
    ext read handles, and restart-status still resolves progress."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    online_uuid = await run_v1_to_success(app, world, baas)
    old_binding_id = ext_of(world, V1)["binding"]["online"]

    baas.delete_bot(online_uuid)

    resp = await api(app, "POST", RESTART.format(publish_id=V1))
    assert resp.json()["success"] is True, resp.text
    await drain(world, until=lambda: len(baas.creates()) == 3)

    restart_op = ledger(world).get_latest_by_kind(V1, "restart", "online")
    assert restart_op.state == PublishOperationState.ABANDONED.value
    fr = ledger(world).get_latest_by_kind(V1, "first_release", "online")
    assert fr.state == PublishOperationState.COMPLETED.value
    new_uuid = fr.bot_uuid
    assert new_uuid != online_uuid and new_uuid in baas.bots

    # NEW binding, old one not reused; ext read handles moved.
    ext = ext_of(world, V1)
    assert ext["binding"]["online"] != old_binding_id
    assert ext["publish"]["online"] == fr.baas_publish_id
    assert ext["restart"]["online"] == fr.baas_publish_id
    binding = world.get(DeviceBindingRepository).get_by_id(ext["binding"]["online"])
    assert binding.device_id == new_uuid

    # Record status untouched by the recreate; restart-status still resolves.
    assert status_of(world, V1) == PublishStatus.SUCCESS.value
    baas.finish_all("SUCCESS")
    resp = await api(app, "POST", RESTART_STATUS.format(publish_id=V1))
    assert resp.status_code == 200, resp.text
    assert status_of(world, V1) == PublishStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_c5_recreate_after_upgrade_chain_keeps_liveness(
    app_with_testing_modules, world
):
    """Kind coexistence: v2's release is an UPGRADE op; the recreate lands a
    FIRST_RELEASE op with a higher workflow id. The liveness predicate keeps
    the max-by-baas_publish_id — the recreated deploy is v2's current one."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    await run_v1_to_success(app, world, baas)
    shared = await _chain_v2_success(app, world, baas)
    assert flow(world).is_current_online_deployment(V2) is True

    baas.delete_bot(shared)
    resp = await api(app, "POST", RESTART.format(publish_id=V2))
    assert resp.json()["success"] is True, resp.text
    await drain(world, until=lambda: len(baas.creates()) == 3)

    fr = ledger(world).get_latest_by_kind(V2, "first_release", "online")
    assert fr.state == PublishOperationState.COMPLETED.value
    up = ledger(world).get_latest_by_kind(V2, "upgrade", "online")
    assert up.state == PublishOperationState.COMPLETED.value
    assert fr.baas_publish_id > up.baas_publish_id
    assert flow(world).is_current_online_deployment(V2) is True
    assert flow(world).is_current_online_deployment(V1) is False
