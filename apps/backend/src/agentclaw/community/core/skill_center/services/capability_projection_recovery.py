"""Find committed capability projection work and wake the existing task queue."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import asdict

from agentclaw.community.core.repository.capability_projection_types import (
    CapabilityProjectionTarget,
)
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityProjectionReconcileProtocol,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.types import Complete, Fail, Retry, TaskOutcome
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env


logger = logging.getLogger(__name__)
_PAGE_SIZE = 100
_SCAN_INTERVAL_SECONDS = 30
_TASK_DEADLINE_SECONDS = 3_600


class CapabilityProjectionRecovery(LifecycleBase):
    """Sweep durable intent even if no task was created or an old task expired.

    Task payloads carry only the Bot's stable address. The same task can cover
    a later desired-state revision, and never resumes a Manifest upload batch.
    """

    task_type = "bot_capability_projection"

    def __init__(
        self, *, repository: CapabilityProjectionRepositoryProtocol,
        reconciler: CapabilityProjectionReconcileProtocol,
        registry: HandlerRegistry, queue: TaskQueueService, worker_enabled: bool,
    ) -> None:
        self._repository = repository
        self._reconciler = reconciler
        self._registry = registry
        self._queue = queue
        self._worker_enabled = worker_enabled
        self._loop_task: asyncio.Task[None] | None = None

    async def bootstrap(self) -> None:
        self._registry.register(self, wake_on_enqueue=True)

    async def startup(self) -> None:
        await asyncio.to_thread(self._repository.validate_schema)
        if self._worker_enabled:
            self._loop_task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

    def reconcile_once(self) -> None:
        env = get_current_env()
        cursor = 0
        while candidates := self._repository.due(
            env=env, limit=_PAGE_SIZE, after_bot_pk=cursor,
        ):
            for address in candidates:
                try:
                    self._queue.enqueue(
                        self.task_type, asdict(address), _TASK_DEADLINE_SECONDS,
                        idempotency_key=f"bot/{address.bot_pk}",
                    )
                except Exception:
                    logger.exception(
                        "Capability projection recovery enqueue failed bot_pk=%s",
                        address.bot_pk,
                    )
            cursor = candidates[-1].bot_pk

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            address = CapabilityProjectionTarget(**(payload or {}))
        except TypeError:
            return Fail("Invalid capability projection recovery address")
        if (
            type(address.bot_pk) is not int
            or address.bot_pk <= 0
            or not all(
                isinstance(value, str) and value
                for value in (address.bot_id, address.entity_id, address.tenant, address.env)
            )
            or address.env != get_current_env()
        ):
            return Fail("Invalid capability projection recovery address")

        with avernet_tenant_scope(address.tenant):
            return asyncio.run(self._recover(address))

    async def _recover(self, address: CapabilityProjectionTarget) -> TaskOutcome:
        locator = {"bot_id": address.bot_id, "entity_id": address.entity_id}
        state = self._repository.read(**locator)
        if state is None or state.bot_pk != address.bot_pk or not state.needs_recovery:
            return Complete()
        await self._reconciler.reconcile(**locator)
        current = self._repository.read(**locator)
        if current is not None and current.bot_pk == address.bot_pk and current.needs_recovery:
            return Retry("Capability projection remains pending")
        return Complete()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.reconcile_once)
            except Exception:
                logger.exception("Capability projection recovery scan failed")
            await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
