"""Durable publish task handlers.

The backend-driven stage advances become persisted, idempotent, self-chaining
``TaskQueueService`` tasks instead of fire-and-forget ``asyncio.create_task``, so
a pod restart mid-build/mid-release resumes instead of stranding the record.

The user-driven ``process`` (or a retry) moves the status forward synchronously
before enqueuing — DRAFT→BUILDING and VALIDATING→ONLINE_PUB — under the optimistic
lock, so a concurrent double-submit loses the CAS and only one task is ever
enqueued. Each task then owns only the remainder, keyed off the record's own
status (the transitions are optimistic-locked and atomic), so a re-run is a
status-guarded checkpoint:

* ``verify_flow``  — build (BUILDING→BUILT) then verify release
  (BUILT→VALIDATE_PUB); a BUILDING crash simply rebuilds. On success enqueues the
  poll.
* ``online_release`` — runs the online release *within* ONLINE_PUB (no self-
  advance); ``ext.publish.online`` presence guards a re-run from creating a second
  bot. On success enqueues the poll.
* ``progress_poll`` — drives the BaaS-publish wait to terminal (VALIDATE_PUB→
  VALIDATING, ONLINE_PUB→SUCCESS) by reusing ``sync_publish_progress``;
  reschedules until the record leaves the ``*_PUB`` state.

The create sub-step's rare "crash between BaaS create and status/ext persist"
window re-creates a bot (the accepted Option-C orphan) — bounded because the
verify create's BUILT→VALIDATE_PUB transition is atomic with the ext write, and
the online create's ``ext.publish.online`` marker is written with the status, so
any re-run at or past that point skips the create.

Failure semantics: a phase that does not reach its expected status (and a missing
publish record) returns ``Fail`` so the task row lands terminally FAILED with the
error in ``last_error`` — mirroring the domain failure the phase already recorded
on the publish record — instead of a semantically dishonest SUCCEEDED. Domain
retry stays user-driven (``retry()`` enqueues a fresh task); the worker never
re-runs a failed stage on its own.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, TaskOutcome
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

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


def enqueue_verify_flow(
    task_queue_service: TaskQueueService, *, publish_id: int, operator: str
) -> None:
    task_queue_service.enqueue(
        VERIFY_FLOW_TASK,
        build_stage_payload(publish_id=publish_id, operator=operator),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def enqueue_online_release(
    task_queue_service: TaskQueueService, *, publish_id: int, operator: str
) -> None:
    task_queue_service.enqueue(
        ONLINE_RELEASE_TASK,
        build_stage_payload(publish_id=publish_id, operator=operator),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def enqueue_progress_poll(
    task_queue_service: TaskQueueService, *, publish_id: int
) -> None:
    task_queue_service.enqueue(
        PROGRESS_POLL_TASK,
        build_poll_payload(publish_id=publish_id),
        deadline_seconds=_POLL_TASK_DEADLINE_SECONDS,
    )


class _PublishTaskBase:
    def __init__(
        self, *, flow: "PublishFlowService", task_queue_service: TaskQueueService
    ) -> None:
        self._flow = flow
        self._task_queue_service = task_queue_service

    def _status(self, publish_id: int):
        record = self._flow.get_publish_record(publish_id)
        if not record:
            return None, None
        return record, PublishStatus(record.status)


class PublishVerifyFlowHandler(_PublishTaskBase):
    """BUILDING → BUILT → VALIDATE_PUB, then enqueue the poll.

    The record enters at BUILDING — the user-driven ``process`` (or a retry) owns
    the preceding DRAFT → BUILDING advance under the optimistic lock, so a
    concurrent double-submit can't reach this task twice. A crash mid-build leaves
    BUILDING and a re-run rebuilds; the BUILT → VALIDATE_PUB transition is the
    idempotency guard that keeps the create/release from re-running.
    """

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
            return Fail(f"publish record not found: publish_id={publish_id}")

        # Each phase records the domain failure on the publish record itself
        # (FAILED + ext.error_message); the Fail outcome mirrors it onto the task
        # so the queue row is semantically honest (FAILED, not SUCCEEDED).
        if status == PublishStatus.BUILDING:
            build_result = await self._flow.execute_build_phase(record, operator)
            if build_result.status != PublishStatus.BUILT:
                return Fail(f"build failed: publish_id={publish_id}, {build_result.message}")
            record, status = self._status(publish_id)

        if status == PublishStatus.BUILT:
            release_result = await self._flow.execute_verify_release_phase(record, operator)
            if release_result.status != PublishStatus.VALIDATE_PUB:
                return Fail(
                    f"verify release failed: publish_id={publish_id}, {release_result.message}"
                )
            record, status = self._status(publish_id)

        # Release submitted (now/previously) → ensure the poll drives it terminal.
        if status == PublishStatus.VALIDATE_PUB:
            enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
        return Complete()


class PublishOnlineReleaseHandler(_PublishTaskBase):
    """Run the online release within ONLINE_PUB, then enqueue the poll.

    The record enters at ONLINE_PUB — the user-driven ``process`` (or a retry) owns
    the go-live VALIDATING → ONLINE_PUB advance, so a concurrent double-submit can't
    reach this task twice. The release runs within ONLINE_PUB (no self-advance);
    ``is_online_release_recorded`` (``ext.publish.online`` presence) is the
    idempotency guard so a crash-resume re-run does not create a second BaaS bot.
    The poll then drives ONLINE_PUB → SUCCESS.
    """

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
            return Fail(f"publish record not found: publish_id={publish_id}")

        # is_online_release_recorded is the crash-resume guard: the release runs
        # within ONLINE_PUB (no self-advance), so the status alone cannot tell a
        # not-yet-run release from one that already created the BaaS bot. Only the
        # ext.publish.online marker (written atomically with the record) does — a
        # lease-expiry re-run of this task must not create a second bot.
        if status == PublishStatus.ONLINE_PUB and not self._flow.is_online_release_recorded(
            publish_id
        ):
            release_result = await self._flow.execute_release_phase(record, operator)
            if release_result.status != PublishStatus.ONLINE_PUB:
                return Fail(
                    f"online release failed: publish_id={publish_id}, {release_result.message}"
                )
            record, status = self._status(publish_id)

        if status == PublishStatus.ONLINE_PUB:
            enqueue_progress_poll(self._task_queue_service, publish_id=publish_id)
        return Complete()


class PublishProgressPollHandler(_PublishTaskBase):
    """Drive a BaaS-publish wait to terminal by reusing ``sync_publish_progress``."""

    def __init__(
        self,
        *,
        flow: "PublishFlowService",
        task_queue_service: TaskQueueService,
        poll_delay_seconds: float = _POLL_DELAY_SECONDS,
    ) -> None:
        super().__init__(flow=flow, task_queue_service=task_queue_service)
        self._poll_delay = poll_delay_seconds

    @property
    def task_type(self) -> str:
        return PROGRESS_POLL_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        record, status = self._status(publish_id)
        if record is None:
            return Fail(f"publish record not found: publish_id={publish_id}")
        if status not in _POLL_ACTIVE_STATES:
            return Complete()  # already advanced / terminal / not a wait state

        # sync_publish_progress advances on BaaS SUCCESS/FAILED (and internally
        # redirects a retry record to restart-sync); a no-op on PENDING.
        sync_result = self._flow.sync_publish_progress(publish_id)

        _record, status = self._status(publish_id)
        if status in _POLL_ACTIVE_STATES:
            return Reschedule(self._poll_delay)  # BaaS not terminal yet
        if status == PublishStatus.FAILED:
            # BaaS reported the publish failed; the record is already FAILED with
            # ext.error_message — mirror it onto the task.
            return Fail(
                f"BaaS publish failed: publish_id={publish_id}, {sync_result.message}"
            )
        return Complete()


class PublishTaskLifecycle(LifecycleBase):
    """Register the durable publish handlers into the shared ``HandlerRegistry``.

    Bound as a singleton ``Lifecycle`` so discovery runs ``bootstrap()`` (which
    populates the registry) before ``TaskWorker.startup()`` claims — mirroring
    ``BaasPublishTaskLifecycle``.
    """

    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        flow: "PublishFlowService",
        task_queue_service: TaskQueueService,
    ) -> None:
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
