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
  advance); the ledger-driven ``is_current_online_deployment`` gate keeps a
  re-run from creating a second bot. On success enqueues the poll.
* ``progress_poll`` — drives the BaaS-publish wait to terminal (VALIDATE_PUB→
  VALIDATING, ONLINE_PUB→SUCCESS) by reusing ``advance_publish_progress``;
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
from typing import TYPE_CHECKING, Callable, Optional

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    DraftRestoreRetryableError,
)
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    Retry,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.publish_approval_service import (
        PublishApprovalService,
    )
    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

logger = get_logger()

VERIFY_FLOW_TASK = "service_bot.publish.verify_flow"
ONLINE_RELEASE_TASK = "service_bot.publish.online_release"
PROGRESS_POLL_TASK = "service_bot.publish.progress_poll"
DRAFT_RESTORE_TASK = "service_bot.publish.draft_restore"
RESTART_TASK = "service_bot.publish.restart"
DESTROY_TASK = "service_bot.publish.destroy"
EVAL_TEARDOWN_TASK = "service_bot.publish.eval_teardown"
APPROVAL_TRIGGER_TASK = "service_bot.publish.approval_trigger"

# Give-up horizons (DB-enforced). Build+release can be slow; the poll waits on the
# BaaS workflow, matching the devices poll deadline.
_STAGE_TASK_DEADLINE_SECONDS = 3600
_POLL_TASK_DEADLINE_SECONDS = 86400
_POLL_DELAY_SECONDS = 8.0
_DRAFT_RESTORE_POLL_DELAY_SECONDS = 2.0
# The operation itself expires at 30 minutes. Keep the queue alive one extra
# minute so a final handler run can persist operation=FAILED before the task
# queue retires the row at its own deadline.
_DRAFT_RESTORE_DEADLINE_SECONDS = 1860

# Eval environments are ephemeral: this is the TTL safety-net horizon after which
# an orphaned eval bot (its quality task never reached to_env_released) is torn
# down. The teardown task's give-up deadline must outlast the delay plus an
# execution window, or the row would be retired before it ever becomes eligible.
_EVAL_TEARDOWN_TTL_SECONDS = 86400
_EVAL_TEARDOWN_DEADLINE_SECONDS = _EVAL_TEARDOWN_TTL_SECONDS + _STAGE_TASK_DEADLINE_SECONDS

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


def enqueue_draft_restore(
    task_queue_service: TaskQueueService,
    *,
    draft_publish_id: int,
    operation_id: int,
    operator: str,
) -> None:
    task_queue_service.enqueue(
        DRAFT_RESTORE_TASK,
        {
            "draft_publish_id": draft_publish_id,
            "operation_id": operation_id,
            "operator": operator,
        },
        deadline_seconds=_DRAFT_RESTORE_DEADLINE_SECONDS,
    )


def build_restart_payload(*, publish_id: int, stage: str, operator: str) -> dict:
    return {"publish_id": publish_id, "stage": stage, "operator": operator}


def enqueue_restart(
    task_queue_service: TaskQueueService, *, publish_id: int, stage: str, operator: str
) -> None:
    task_queue_service.enqueue(
        RESTART_TASK,
        build_restart_payload(publish_id=publish_id, stage=stage, operator=operator),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def enqueue_destroy(
    task_queue_service: TaskQueueService, *, publish_id: int, stage: str, operator: str
) -> None:
    task_queue_service.enqueue(
        DESTROY_TASK,
        build_restart_payload(publish_id=publish_id, stage=stage, operator=operator),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def build_approval_trigger_payload(*, publish_id: int, action: str, operator: str) -> dict:
    return {"publish_id": publish_id, "action": action, "operator": operator}


def enqueue_approval_trigger(
    task_queue_service: TaskQueueService,
    *,
    publish_id: int,
    action: str,
    operator: str,
) -> None:
    """Enqueue the durable AGREED-trigger (online release / offline)."""
    task_queue_service.enqueue(
        APPROVAL_TRIGGER_TASK,
        build_approval_trigger_payload(
            publish_id=publish_id, action=action, operator=operator
        ),
        deadline_seconds=_STAGE_TASK_DEADLINE_SECONDS,
    )


def build_eval_teardown_payload(*, publish_id: int, bot_uuid: str, operator: str) -> dict:
    return {"publish_id": publish_id, "bot_uuid": bot_uuid, "operator": operator}


def enqueue_eval_teardown(
    task_queue_service: TaskQueueService,
    *,
    publish_id: int,
    bot_uuid: str,
    operator: str,
    delay_seconds: int = 0,
) -> None:
    """Enqueue the durable eval teardown. ``delay_seconds`` = the TTL safety net
    at publish time; ``0`` for an explicit (post-eval) early teardown."""
    task_queue_service.enqueue(
        EVAL_TEARDOWN_TASK,
        build_eval_teardown_payload(
            publish_id=publish_id, bot_uuid=bot_uuid, operator=operator
        ),
        deadline_seconds=_EVAL_TEARDOWN_DEADLINE_SECONDS,
        delay_seconds=delay_seconds,
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
    the ledger-driven ``is_current_online_deployment`` gate is the idempotency
    guard: the release is skipped only when this record's release is the current
    live deployment on its bot, so a crash-resume re-run does not create a second
    BaaS bot and a stale/failed release re-runs. The poll then drives
    ONLINE_PUB → SUCCESS.
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

        # is_current_online_deployment is the crash-resume guard: the release runs
        # within ONLINE_PUB (no self-advance), so the status alone cannot tell a
        # not-yet-run release from one that already created the BaaS bot. Only the
        # ledger's bot timeline does — a lease-expiry re-run of this task must not
        # create a second bot, and a stale or failed release must re-run.
        if status == PublishStatus.ONLINE_PUB and not self._flow.is_current_online_deployment(
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


class PublishRestartHandler(_PublishTaskBase):
    """Durable Bot restart (re-deploy) — replaces the old fire-and-forget
    ``asyncio.create_task``.

    The restart work runs through the operation runner (``execute_restart``), so a
    crash-resume adopts the in-doubt restart workflow (existing bot) instead of
    issuing a second one. Approval is server-side (all-auto). Progress stays
    user-driven via ``sync_restart_progress`` (``ext.restart.<stage>``, written by
    the runner step), so no poll is enqueued here."""

    @property
    def task_type(self) -> str:
        return RESTART_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        stage = _require_str(payload, "stage")
        operator = _require_str(payload, "operator")
        return asyncio.run(self._run(publish_id, stage, operator))

    async def _run(self, publish_id: int, stage: str, operator: str) -> TaskOutcome:
        result = await self._flow.execute_restart(
            publish_id=publish_id, stage=stage, operator=operator
        )
        if not result or not result.get("success"):
            message = (result or {}).get("message", "unknown error")
            return Fail(f"restart failed: publish_id={publish_id}, {message}")
        return Complete()


class PublishDestroyHandler(_PublishTaskBase):
    """Durable bot destroy (offline) — replaces the fire-and-forget background
    destroy. Idempotent via ``execute_offline_destroy``: a RELEASED binding
    short-circuits (destroy already ran) and ``stop_bot`` is idempotent
    server-side, so a re-delivery is a no-op. A genuine ``stop_bot`` failure
    propagates so the task retries rather than masking it as done."""

    @property
    def task_type(self) -> str:
        return DESTROY_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        stage = _require_str(payload, "stage")
        operator = _require_str(payload, "operator")
        return asyncio.run(self._run(publish_id, stage, operator))

    async def _run(self, publish_id: int, stage: str, operator: str) -> TaskOutcome:
        # execute_offline_destroy raises on a real BaaS/ledger failure → the
        # exception propagates out of asyncio.run and the queue retries the task
        # (resuming the same non-terminal op → adopt), so a transient destroy
        # failure is no longer silently completed.
        result = await self._flow.execute_offline_destroy(
            publish_id=publish_id, stage=stage, operator=operator
        )
        if not result or not result.get("success"):
            return Fail(f"destroy failed: publish_id={publish_id}, {(result or {}).get('message')}")
        return Complete()


class PublishEvalTeardownHandler(_PublishTaskBase):
    """Durable eval-environment teardown — idempotent via the ``eval_teardown``
    operation runner op (existing bot → adopt-by-query, never a second destroy).

    Two enqueues converge here: the TTL safety net from ``eval_publish`` (delayed)
    and the explicit post-eval teardown (delay 0). Both key on the same op, so
    whichever runs first destroys and completes the op; the other adopts the
    recorded DESTROY workflow and is a no-op."""

    @property
    def task_type(self) -> str:
        return EVAL_TEARDOWN_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        bot_uuid = _require_str(payload, "bot_uuid")
        operator = _require_str(payload, "operator")
        return asyncio.run(self._run(publish_id, bot_uuid, operator))

    async def _run(self, publish_id: int, bot_uuid: str, operator: str) -> TaskOutcome:
        result = await self._flow.execute_eval_teardown(
            publish_id=publish_id, bot_uuid=bot_uuid, operator=operator
        )
        if not result or not result.get("success"):
            message = (result or {}).get("message", "unknown error")
            return Fail(f"eval teardown failed: bot_uuid={bot_uuid}, {message}")
        return Complete()


class PublishApprovalTriggerHandler:
    """Durable AGREED-trigger — replaces the inline await in the approval callback.

    Runs the online-release / offline trigger through the approval service, whose
    status-CAS-guarded ``process()``/offline makes an AGREED-then-crash converge on
    retry and a duplicate callback delivery a no-op. Holds a lazy provider to break
    the approval-service ↔ flow-service DI cycle."""

    def __init__(
        self, *, approval_service_provider: Callable[[], "PublishApprovalService"]
    ) -> None:
        self._approval_service_provider = approval_service_provider

    @property
    def task_type(self) -> str:
        return APPROVAL_TRIGGER_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        publish_id = _require_int(payload, "publish_id")
        action = _require_str(payload, "action")
        operator = _require_str(payload, "operator")
        return asyncio.run(self._run(publish_id, action, operator))

    async def _run(self, publish_id: int, action: str, operator: str) -> TaskOutcome:
        approval_service = self._approval_service_provider()
        result = await approval_service.execute_approval_trigger(
            publish_id=publish_id, action=action, operator=operator
        )
        if not result or not result.get("success"):
            message = (result or {}).get("message", "unknown error")
            return Fail(f"approval trigger failed: publish_id={publish_id}, {message}")
        return Complete()


class PublishProgressPollHandler(_PublishTaskBase):
    """Drive a BaaS-publish wait to terminal by reusing ``advance_publish_progress``."""

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

        # advance_publish_progress advances on BaaS SUCCESS/FAILED (and internally
        # redirects a retry record to restart-sync); a no-op on PENDING.
        sync_result = self._flow.advance_publish_progress(publish_id)

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


class PublishDraftRestoreHandler(_PublishTaskBase):
    """Run or resume one draft restore attempt from its durable ledger row."""

    def __init__(
        self,
        *,
        flow: "PublishFlowService",
        task_queue_service: TaskQueueService,
        poll_delay_seconds: float = _DRAFT_RESTORE_POLL_DELAY_SECONDS,
    ) -> None:
        super().__init__(flow=flow, task_queue_service=task_queue_service)
        self._poll_delay = poll_delay_seconds

    @property
    def task_type(self) -> str:
        return DRAFT_RESTORE_TASK

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        draft_publish_id = _require_int(payload, "draft_publish_id")
        operation_id = _require_int(payload, "operation_id")
        operator = _require_str(payload, "operator")
        return asyncio.run(
            self._run(draft_publish_id, operation_id, operator)
        )

    async def _run(
        self, draft_publish_id: int, operation_id: int, operator: str
    ) -> TaskOutcome:
        try:
            result = await self._flow.execute_restore_draft(
                draft_publish_id=draft_publish_id,
                operation_id=operation_id,
                operator=operator,
            )
        except DraftRestoreRetryableError as exc:
            return Retry(
                "draft restore temporarily unavailable; retrying the same operation: "
                f"publish_id={draft_publish_id}, operation_id={operation_id}, {exc}"
            )
        except Exception as exc:
            return Fail(
                "draft restore failed: "
                f"publish_id={draft_publish_id}, operation_id={operation_id}, {exc}"
            )
        if result.get("status") == "restoring":
            return Reschedule(self._poll_delay)
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
        approval_service_provider: Callable[[], "PublishApprovalService"],
    ) -> None:
        self._registry = registry
        self._flow = flow
        self._task_queue_service = task_queue_service
        self._approval_service_provider = approval_service_provider

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
            PublishRestartHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
        self._registry.register(
            PublishDestroyHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
        self._registry.register(
            PublishEvalTeardownHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
        self._registry.register(
            PublishApprovalTriggerHandler(
                approval_service_provider=self._approval_service_provider
            )
        )
        self._registry.register(
            PublishProgressPollHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
        self._registry.register(
            PublishDraftRestoreHandler(
                flow=self._flow, task_queue_service=self._task_queue_service
            )
        )
