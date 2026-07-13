"""End-to-end durable-pipeline integration (Task 14).

Drives the full ``DRAFT → … → VALIDATING`` pipeline through the persisted task
queue + worker (not inline), proving the pipeline advances autonomously and
idempotently and that the poll never crosses the manual go-live gate. Reuses the
endpoint harness's in-memory SQLite + BaaS HTTP stub; the worker is driven
deterministically via ``run_once()`` (the test profile keeps the background loop
off).
"""
import httpx
import pytest
from httpx import ASGITransport

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    VERIFY_FLOW_TASK,
    PublishTaskLifecycle,
    enqueue_verify_flow,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.worker import TaskWorker
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from tests.community.endpoints.test_service_bot_publish_flow import (
    _HEADERS,
    _PROCESS,
    _V1,
    _ext,
    _install_baas,
    _install_engine,
    _posts,
    _seed_draft,
    _seed_validating,
    _status,
)

pytestmark = pytest.mark.integration


async def _post_process(app) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(_PROCESS, json={"publish_id": _V1}, headers=_HEADERS)


async def _drive_worker(world, *, max_ticks: int = 15) -> None:
    """Register the publish handlers (once) and tick the worker until the queue
    has nothing due (a rescheduled poll parks in the future → run_once returns 0)."""
    injector = world.injector
    registry = injector.get(HandlerRegistry)
    if registry.get(VERIFY_FLOW_TASK) is None:
        await injector.get(PublishTaskLifecycle).bootstrap()
    worker = injector.get(TaskWorker)
    for _ in range(max_ticks):
        if await worker.run_once() == 0:
            break


def _create_count(world) -> int:
    return sum(1 for p in _posts(world) if p.endswith("/api/v1/bots"))


@pytest.mark.asyncio
async def test_draft_process_drives_to_validating_via_worker(
    app_with_testing_modules, world
):
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    _install_engine(world)

    resp = await _post_process(app_with_testing_modules)
    assert resp.status_code == 200
    # /process advances DRAFT -> BUILDING synchronously (the double-submit guard),
    # then enqueues the durable verify_flow task for the remainder.
    assert resp.json()["data"]["status"] == "building"
    assert _status(world, _V1) == PublishStatus.BUILDING.value

    await _drive_worker(world)

    # verify_flow (build+create → VALIDATE_PUB) then progress_poll (BaaS SUCCESS →
    # VALIDATING) ran autonomously through the durable queue.
    assert _status(world, _V1) == PublishStatus.VALIDATING.value
    # The manual gate held: the poll parked at VALIDATING, never crossed to ONLINE.
    assert _status(world, _V1) != PublishStatus.ONLINE_PUB.value
    # Exactly one BaaS bot create.
    assert _create_count(world) == 1


@pytest.mark.asyncio
async def test_validating_process_drives_online_leg_to_success_via_worker(
    app_with_testing_modules, world
):
    """The go-live leg end-to-end through the durable queue: /process advances
    VALIDATING → ONLINE_PUB synchronously and enqueues online_release; the worker
    runs the release within ONLINE_PUB, then the poll sees BaaS SUCCESS and lands
    the record at SUCCESS with the online binding ACTIVE. (This coverage moved
    here from the endpoint /sync cases when /sync became a read-only report.)"""
    _seed_validating(world)
    _install_baas(world, progress_status="SUCCESS")
    _install_engine(world)

    resp = await _post_process(app_with_testing_modules)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "online_pub"
    assert _status(world, _V1) == PublishStatus.ONLINE_PUB.value

    await _drive_worker(world)

    assert _status(world, _V1) == PublishStatus.SUCCESS.value
    online_binding_id = _ext(world, _V1)["binding"]["online"]
    binding = world.get(DeviceBindingRepository).get_by_id(online_binding_id)
    assert binding.status == "ACTIVE", binding.status


@pytest.mark.asyncio
async def test_verify_flow_rerun_does_not_double_create(
    app_with_testing_modules, world
):
    _seed_draft(world)
    # BaaS stays PENDING → the poll never advances; the record parks at VALIDATE_PUB.
    _install_baas(world, progress_status="PENDING")
    _install_engine(world)

    await _post_process(app_with_testing_modules)
    await _drive_worker(world)
    assert _status(world, _V1) == PublishStatus.VALIDATE_PUB.value
    assert _create_count(world) == 1

    # Crash-resume simulation: re-enqueue verify_flow for the same record and
    # drive again. The status guard (record already at VALIDATE_PUB) skips both
    # the rebuild and the create.
    enqueue_verify_flow(
        world.injector.get(TaskQueueService), publish_id=_V1, operator="10001"
    )
    await _drive_worker(world)

    assert _status(world, _V1) == PublishStatus.VALIDATE_PUB.value
    assert _create_count(world) == 1  # no second bot created
