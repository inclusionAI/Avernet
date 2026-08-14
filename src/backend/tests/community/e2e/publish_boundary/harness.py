"""Cross-publish-boundary DI-world harness.

Production wiring end-to-end: the real FastAPI app on a per-test TEST-profile
injector (fresh in-memory SQLite), real ``PublishFlowService`` / task handlers /
``BotBuildService`` (teclaw compose+freeze) / repositories — with local
implementations only at the system boundaries. BaaS is the stateful
:class:`~tests.community.e2e.publish_boundary.local_baas.LocalBaas` at the HTTP
seam; object storage / engine-ext / approval use the existing ``plugins/local``
implementations wired by the TEST profile.

Flows are driven through the public publish endpoints; the durable tasks run
deterministically by ticking ``TaskWorker.run_once()`` to quiescence between
steps (:func:`drain`). Bot/skills seeding is reused verbatim from the endpoint
cases (``test_service_bot_publish_flow``) — the same source of truth the
endpoint suite trusts.
"""
from __future__ import annotations

import httpx
from httpx import ASGITransport

from agentclaw.community.core.repository.protocols.publishing import PublishOperationRepository
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PROGRESS_POLL_TASK,
    VERIFY_FLOW_TASK,
    PublishProgressPollHandler,
    PublishTaskLifecycle,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.worker import TaskWorker

# Re-exported seeds/paths: the endpoint cases' seeding is the source of truth
# for a valid teclaw bot + skills + DRAFT publish record (same import pattern
# as tests/endpoints/test_publish_durable_pipeline.py).
from tests.community.endpoints.test_service_bot_publish_flow import (  # noqa: F401
    _HEADERS as HEADERS,
    _OWNER as OWNER,
    _PROCESS as PROCESS,
    _RETRY as RETRY,
    _UPGRADE as UPGRADE,
    _V1 as V1,
    _ext as ext_of,
    _install_engine as install_engine,
    _seed_draft as seed_draft,
    _status as status_of,
)
from tests.community.e2e.publish_boundary.local_baas import LocalBaas

RESTART = "/api/service-bot/publish/{publish_id}/restart"
ROLLBACK = "/api/service-bot/publish/{publish_id}/rollback"


def install_local_baas(world) -> LocalBaas:
    return LocalBaas().install(world)


async def api(app, method: str, path: str, *, json: dict | None = None) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json, headers=HEADERS)


async def _ensure_handlers(world) -> None:
    """Register the durable publish handlers once, with a ZERO-delay poll.

    The production poll reschedules ``_POLL_DELAY_SECONDS`` (8s) into the
    future, which would park a still-ACTIVE workflow beyond the drain's
    horizon. The handler already takes ``poll_delay_seconds`` for exactly this
    knob; the registry forbids duplicate registration, so the zero-delay poll
    handler is swapped in after bootstrap (test-harness liberty, registry map
    only)."""
    injector = world.injector
    registry = injector.get(HandlerRegistry)
    if registry.get(VERIFY_FLOW_TASK) is not None:
        return
    await injector.get(PublishTaskLifecycle).bootstrap()
    registry._handlers[PROGRESS_POLL_TASK] = PublishProgressPollHandler(
        flow=injector.get(PublishFlowService),
        task_queue_service=injector.get(TaskQueueService),
        poll_delay_seconds=0.0,
    )


async def drain(world, *, until=None, max_ticks: int = 30) -> None:
    """Tick the worker until the queue is quiescent, ``until()`` holds, or the
    tick budget runs out. With the zero-delay poll, a record parked in a BaaS
    wait state keeps its poll due — pass ``until`` for those phases and flip
    the LocalBaas workflow before draining further."""
    await _ensure_handlers(world)
    worker = world.injector.get(TaskWorker)
    for _ in range(max_ticks):
        if until is not None and until():
            return
        if await worker.run_once() == 0:
            return


def ledger(world) -> PublishOperationRepository:
    return world.get(PublishOperationRepository)


def flow(world) -> PublishFlowService:
    return world.get(PublishFlowService)
