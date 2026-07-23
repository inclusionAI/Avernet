"""R1-R6 — retry, deploy-failure outcome, and cross-record liveness, end-to-end.

The regression class this session fixes, proven through the production wiring:
a failed online deploy is outcome-corrected in the ledger, retry always
re-drives the release path (which re-issues a fresh attempt), a failed deploy
never falsely supersedes the live release, and the restart branches
(VALIDATE_PUB / SUCCESS) still restart. All flows run over the shared online
bot across multiple publish records.
"""
import pytest

from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    enqueue_online_release,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)

from tests.community.e2e.publish_boundary.harness import (
    OWNER,
    PROCESS,
    RESTART,
    RETRY,
    UPGRADE,
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

pytestmark = pytest.mark.integration

V2 = 2  # the upgrade record's id (second ac_bot_publish row)
RESTART_STATUS = "/api/service-bot/publish/{publish_id}/restart_status"


async def _upgrade_to_online_pub(app, world, baas) -> str:
    """From v1 SUCCESS: create the v2 upgrade record and drive it to ONLINE_PUB
    with its online upgrade issued (workflow ACTIVE) on the shared online bot.
    Returns the shared online bot uuid."""
    online_uuid = ext_of(world, V1)["binding"]["online"]  # binding id, resolve uuid
    resp = await api(app, "POST", UPGRADE, json={"publish_id": V1})
    assert resp.json()["success"] is True, resp.text
    assert status_of(world, V2) == PublishStatus.DRAFT.value

    # v2: build + its own verify bot, then go-live issues the UPGRADE on the
    # shared online bot (v1 SUCCESS ⇒ _should_upgrade_online).
    await api(app, "POST", PROCESS, json={"publish_id": V2})
    await drain(world, until=lambda: status_of(world, V2) == PublishStatus.VALIDATE_PUB.value)
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.VALIDATING.value

    await api(app, "POST", PROCESS, json={"publish_id": V2})
    await drain(world, until=lambda: (ext_of(world, V2).get("publish") or {}).get("online"))

    up = ledger(world).get_latest_by_kind(V2, "upgrade", "online")
    assert up is not None and up.baas_publish_id is not None
    shared_uuid = up.bot_uuid
    assert baas.latest_workflow(shared_uuid)["publish_type"] == "UPDATE"
    return shared_uuid


@pytest.mark.asyncio
async def test_r1_r2_online_wait_failure_retry_reissues_and_never_falsely_supersedes(
    app_with_testing_modules, world
):
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    await run_v1_to_success(app, world, baas)
    shared = await _upgrade_to_online_pub(app, world, baas)
    assert baas.updates_of(shared) == 1

    # The upgrade's BaaS wait FAILS → poll marks v2 FAILED and outcome-corrects
    # the UPGRADE op (the ledger now reflects the deploy's true outcome).
    baas.finish_all("FAILED")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.FAILED.value
    up = ledger(world).get_latest_by_kind(V2, "upgrade", "online")
    assert up.state == PublishOperationState.FAILED.value

    # R2 — the failed deploy never supersedes: v1 is still the live deployment.
    assert flow(world).is_current_online_deployment(V1) is True
    assert flow(world).is_current_online_deployment(V2) is False
    assert status_of(world, V1) == PublishStatus.SUCCESS.value

    # R1 — retry re-drives the release path: a SECOND upgrade reaches BaaS as a
    # fresh ledger attempt (no skip, no loop, no restart).
    resp = await api(app, "POST", RETRY.format(publish_id=V2))
    assert resp.json()["success"] is True, resp.text
    assert status_of(world, V2) == PublishStatus.ONLINE_PUB.value
    await drain(world, until=lambda: baas.updates_of(shared) == 2)
    assert baas.updates_of(shared) == 2
    up2 = ledger(world).get_latest_by_kind(V2, "upgrade", "online")
    assert up2.attempt == up.attempt + 1

    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.SUCCESS.value
    # Now v2 genuinely supersedes v1.
    assert status_of(world, V1) == PublishStatus.UPGRADED.value
    assert flow(world).is_current_online_deployment(V2) is True
    assert flow(world).is_current_online_deployment(V1) is False
    # No restart was ever issued; no third bot was ever created — v2's verify
    # leg UPGRADES the existing verify bot (BOT-1) rather than creating one,
    # and the online leg shares BOT-2 across both records.
    assert ledger(world).get_latest_by_kind(V2, "restart", "online") is None
    assert baas.creates() == ["BOT-1", "BOT-2"]
    assert shared in baas.bots


@pytest.mark.asyncio
async def test_r3_retry_interleaved_with_restart_on_shared_bot(
    app_with_testing_modules, world
):
    """Between v2's failure and its retry, a RESTART of live v1 lands on the
    shared bot (a later, non-version-setting workflow). The retry must still
    re-run v2's release — the restart neither supersedes v1 nor confuses the
    gate — and exactly one new deploy lands from the retry."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    await run_v1_to_success(app, world, baas)
    shared = await _upgrade_to_online_pub(app, world, baas)

    baas.finish_all("FAILED")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.FAILED.value

    # Interleave: restart live v1 (SUCCESS) → a RESTART workflow on the shared
    # bot, driven by the durable restart task.
    resp = await api(app, "POST", RESTART.format(publish_id=V1))
    assert resp.json()["success"] is True, resp.text
    await drain(world, until=lambda: baas.updates_of(shared) == 2)
    assert baas.updates_of(shared) == 2
    baas.finish_all("SUCCESS")
    # v1 is still live and current: the restart does not set the version.
    assert flow(world).is_current_online_deployment(V1) is True

    # v2's retry re-runs its release over the shared bot.
    await api(app, "POST", RETRY.format(publish_id=V2))
    await drain(world, until=lambda: baas.updates_of(shared) == 3)
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V2) == PublishStatus.SUCCESS.value
    assert flow(world).is_current_online_deployment(V2) is True
    assert len(baas.creates()) == 2  # no bot was ever duplicated
    assert shared in baas.bots


@pytest.mark.asyncio
async def test_r4_verify_wait_failure_retry_still_restarts(
    app_with_testing_modules, world
):
    """Deferred-symmetry guard: a VALIDATE_PUB (verify BaaS-wait) failure
    retries via the restart branch — a RESTART op at the verify stage, the
    retry-flag poll redirect, and NO verify release re-run."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)

    await api(app, "POST", PROCESS, json={"publish_id": V1})
    await drain(world, until=lambda: status_of(world, V1) == PublishStatus.VALIDATE_PUB.value)
    verify_uuid = ledger(world).get_latest_by_kind(V1, "first_release", "verify").bot_uuid

    baas.finish_all("FAILED")
    await drain(world)
    assert status_of(world, V1) == PublishStatus.FAILED.value
    # The verify create's op was outcome-corrected.
    fr = ledger(world).get_latest_by_kind(V1, "first_release", "verify")
    assert fr.state == PublishOperationState.FAILED.value

    resp = await api(app, "POST", RETRY.format(publish_id=V1))
    assert resp.json()["success"] is True, resp.text
    # Restart branch: inline execute_restart issued an UPDATE on the verify bot
    # and the rollback write carries the poll-redirect flag.
    assert baas.updates_of(verify_uuid) == 1
    restart_op = ledger(world).get_latest_by_kind(V1, "restart", "verify")
    assert restart_op.state == PublishOperationState.COMPLETED.value
    assert ext_of(world, V1).get("retry") is True
    # No verify release re-run: the FIRST_RELEASE op did not grow an attempt.
    assert ledger(world).get_latest_by_kind(V1, "first_release", "verify").attempt == fr.attempt

    # The redirected poll settles the restart → back to VALIDATING.
    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V1) == PublishStatus.VALIDATING.value
    assert "retry" not in ext_of(world, V1)


@pytest.mark.asyncio
async def test_r5_success_record_retry_rerestarts_never_touches_release_gate(
    app_with_testing_modules, world
):
    """A live record only fails via a failed restart-sync; its retry
    re-restarts (fresh RESTART attempt). Routing it through the release path
    would skip BaaS entirely — the exact misuse the gate-only predicate
    forbids."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)
    online_uuid = await run_v1_to_success(app, world, baas)
    online_op_before = ledger(world).get_latest_by_kind(V1, "first_release", "online")

    # Restart the live bot; its workflow FAILS; the user-driven restart_status
    # sync marks the record FAILED (source SUCCESS) and outcome-corrects the op.
    await api(app, "POST", RESTART.format(publish_id=V1))
    await drain(world, until=lambda: baas.updates_of(online_uuid) == 1)
    baas.finish_all("FAILED")
    resp = await api(app, "POST", RESTART_STATUS.format(publish_id=V1))
    assert resp.status_code == 200, resp.text
    assert status_of(world, V1) == PublishStatus.FAILED.value
    r1 = ledger(world).get_latest_by_kind(V1, "restart", "online")
    assert r1.state == PublishOperationState.FAILED.value

    # Retry → restart branch: a fresh RESTART attempt re-deploys via BaaS.
    resp = await api(app, "POST", RETRY.format(publish_id=V1))
    assert resp.json()["success"] is True, resp.text
    assert baas.updates_of(online_uuid) == 2
    r2 = ledger(world).get_latest_by_kind(V1, "restart", "online")
    assert r2.attempt == r1.attempt + 1
    # The release gate was never consulted into action: the record's release op
    # is untouched (same attempt, still COMPLETED, still the live deployment).
    online_op_after = ledger(world).get_latest_by_kind(V1, "first_release", "online")
    assert online_op_after.attempt == online_op_before.attempt
    assert online_op_after.state == PublishOperationState.COMPLETED.value

    baas.finish_all("SUCCESS")
    await api(app, "POST", RESTART_STATUS.format(publish_id=V1))
    assert status_of(world, V1) == PublishStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_r6_online_release_redelivery_does_not_reissue(
    app_with_testing_modules, world
):
    """Lease-expiry redelivery of online_release while the deploy is in flight:
    the gate reads the record's landed (bookkeeping-complete) release as current
    and skips — no second issue, the poll settles the original workflow."""
    app = app_with_testing_modules
    seed_draft(world)
    install_engine(world)
    baas = install_local_baas(world)

    await api(app, "POST", PROCESS, json={"publish_id": V1})
    await drain(world, until=lambda: status_of(world, V1) == PublishStatus.VALIDATE_PUB.value)
    baas.finish_all("SUCCESS")
    await drain(world)
    await api(app, "POST", PROCESS, json={"publish_id": V1})
    await drain(world, until=lambda: (ext_of(world, V1).get("publish") or {}).get("online"))
    assert baas.bot_count() == 2
    creates_before = len(baas.creates())

    # Simulated redelivery: a duplicate online_release task for the same record.
    enqueue_online_release(
        world.get(TaskQueueService), publish_id=V1, operator=OWNER
    )
    await drain(world, until=lambda: False, max_ticks=6)

    assert len(baas.creates()) == creates_before  # no second bot / no re-issue
    assert baas.bot_count() == 2

    baas.finish_all("SUCCESS")
    await drain(world)
    assert status_of(world, V1) == PublishStatus.SUCCESS.value
