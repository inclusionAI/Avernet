"""Durable publish task handlers.

The backend-driven stage advances become persisted, idempotent, self-chaining
``TaskQueueService`` tasks instead of fire-and-forget ``asyncio.create_task``, so
a pod restart mid-build/mid-release resumes instead of stranding the record.

Idempotency is keyed off the record's own status (the transitions are
optimistic-locked and atomic), so a re-run is a status-guarded checkpoint:

* ``verify_flow``  — build (DRAFT→BUILT) then verify release (BUILT→VALIDATE_PUB);
  a BUILDING crash resets to DRAFT and rebuilds. On success enqueues the poll.
* ``online_release`` — the online release (VALIDATING→ONLINE_PUB). On success
  enqueues the poll. Only a user ``/process`` enqueues this (the go-live gate).
* ``progress_poll`` — drives the BaaS-publish wait to terminal (VALIDATE_PUB→
  VALIDATING, ONLINE_PUB→SUCCESS) by reusing ``sync_publish_progress``;
  reschedules until the record leaves the ``*_PUB`` state.

The create sub-step's rare "crash between BaaS create and status persist" window
re-creates a bot (the accepted Option-C orphan) — bounded because the
status→``*_PUB`` transition is atomic with the ext write, so any re-run at or past
``*_PUB`` skips the create.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Reschedule, TaskOutcome
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

VERIFY_FLOW_TASK = "service_bot.publish.verify_flow"
ONLINE_RELEASE_TASK = "service_bot.publish.online_release"
PROGRESS_POLL_TASK = "service_bot.publish.progress_poll"

# Give-up horizons (DB-enforced). Build+release can be slow; the poll waits on the
# BaaS workflow, matching the devices poll deadline.
_STAGE_TASK_DEADLINE_SECONDS = 3600
_POLL_TASK_DEADLINE_SECONDS = 86400
_POLL_DELAY_SECONDS = 8.0

# States still waiting on a BaaS publish → the poll keeps driving them.
_POLL_ACTIVE_STATES = {PublishStatus.VALIDATE_PUB, PublishStatus.ONLINE_PUB}


def _require_int(payload: Optional[dict], key: str) -> int:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"field {key} must be int")
    return value


def _require_str(payload: Optional[dict], key: str) -> str:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"missing required field: {key}")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"field {key} must be str")
    return value


def build_stage_payload(*, publish_id: int, operator: str) -> dict:
    return {"publish_id": publish_id, "operator": operator}


def build_poll_payload(*, publish_id: int) -> dict:
    return {"publish_id": publish_id}


def enqueue_verify_flow(task_queue_service, *, publish_id: int, operator: str) -> None:
    task_queue_service.enqueue(
        VERIFY_FLOW_TASK,
        build_stage_payload(publish_id=publish_id, operator=operator),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def enqueue_online_release(task_queue_service, *, publish_id: int, operator: str) -> None:
    task_queue_service.enqueue(
        ONLINE_RELEASE_TASK,
        build_stage_payload(publish_id=publish_id, operator=operator),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def enqueue_progress_poll(task_queue_service, *, publish_id: int) -> None:
    task_queue_service.enqueue(
        PROGRESS_POLL_TASK,
        build_poll_payload(publish_id=publish_id),
        deadline_seconds=_POLL_TASK_DEADLINE_SECONDS,
    )


class _PublishTaskBase:
    def __init__(self, *, flow, task_queue_service) -> None:
        self._flow = flow
        self._task_queue_service = task_queue_service

    def _status(self, publish_id: int):
        record = self._flow._publish_service.get_publish_by_id(publish_id)
        if not record:
            return None, None
        return record, PublishStatus(record.status)


class PublishVerifyFlowHandler(_PublishTaskBase):
    """DRAFT → BUILDING → BUILT → VALIDATE_PUB, then enqueue the poll."""

    @property
    def task_type(self) -> str:
        return VERIFY_FLOW_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        operator = _require_str(payload, "operator")
        return asyncio.run(self._run(publish_id, operator))

    async def _run(self, publish_id: int, operator: str) -> TaskOutcome:
        record, status = self._status(publish_id)
        if record is None:
            return Complete()

        # Crash mid-build (left at BUILDING): reset to DRAFT so build re-runs
        # cleanly under the optimistic lock, then rebuild.
        if status == PublishStatus.BUILDING:
            try:
                self._flow._publish_service.update_publish_status(
                    publish_id,
                    PublishStatus.DRAFT.value,
                    PublishStatus.BUILDING.value,
                )
                status = PublishStatus.DRAFT
            except Exception:
                record, status = self._status(publish_id)

        if status == PublishStatus.DRAFT:
            build_result = await self._flow._execute_build_phase(record, operator)
            if build_result.status != PublishStatus.BUILT:
                return Complete()  # build failed → FAILED already recorded
            record, status = self._status(publish_id)

        if status == PublishStatus.BUILT:
            await self._flow._execute_verify_release_phase(record, operator)
            record, status = self._status(publish_id)

        # Release submitted (now/previously) → ensure the poll drives it terminal.
        if status == PublishStatus.VALIDATE_PUB:
            enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
        return Complete()


class PublishOnlineReleaseHandler(_PublishTaskBase):
    """VALIDATING → ONLINE_PUB (the go-live gate's work), then enqueue the poll."""

    @property
    def task_type(self) -> str:
        return ONLINE_RELEASE_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        operator = _require_str(payload, "operator")
        return asyncio.run(self._run(publish_id, operator))

    async def _run(self, publish_id: int, operator: str) -> TaskOutcome:
        record, status = self._status(publish_id)
        if record is None:
            return Complete()

        if status == PublishStatus.VALIDATING:
            await self._flow._execute_release_phase(record, operator)
            record, status = self._status(publish_id)

        if status == PublishStatus.ONLINE_PUB:
            enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
        return Complete()


class PublishProgressPollHandler(_PublishTaskBase):
    """Drive a BaaS-publish wait to terminal by reusing ``sync_publish_progress``."""

    def __init__(self, *, flow, task_queue_service, poll_delay_seconds: float = _POLL_DELAY_SECONDS) -> None:
        super().__init__(flow=flow, task_queue_service=task_queue_service)
        self._poll_delay = poll_delay_seconds

    @property
    def task_type(self) -> str:
        return PROGRESS_POLL_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        record, status = self._status(publish_id)
        if record is None or status not in _POLL_ACTIVE_STATES:
            return Complete()  # already advanced / terminal / not a wait state

        # sync_publish_progress advances on BaaS SUCCESS/FAILED (and internally
        # redirects a retry record to restart-sync); a no-op on PENDING.
        self._flow.sync_publish_progress(publish_id)

        _record, status = self._status(publish_id)
        if status in _POLL_ACTIVE_STATES:
            return Reschedule(self._poll_delay)  # BaaS not terminal yet
        return Complete()


class PublishTaskLifecycle(LifecycleBase):
    """Register the durable publish handlers into the shared ``HandlerRegistry``.

    Bound as a singleton ``Lifecycle`` so discovery runs ``bootstrap()`` (which
    populates the registry) before ``TaskWorker.startup()`` claims — mirroring
    ``BaasPublishTaskLifecycle``.
    """

    def __init__(self, *, registry: HandlerRegistry, flow, task_queue_service) -> None:
        self._registry = registry
        self._flow = flow
        self._task_queue_service = task_queue_service

    async def bootstrap(self) -> None:
        self._registry.register(
            PublishVerifyFlowHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
        self._registry.register(
            PublishOnlineReleaseHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
        self._registry.register(
            PublishProgressPollHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
