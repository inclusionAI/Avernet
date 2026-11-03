"""TaskQueue delivery and journal reconciliation without credential payloads."""

import asyncio

from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    OwnerHandoffServiceProtocol,
    OwnerHandoffPolicy,
)
from agentclaw.community.core.bot_management.owner_handoff.service import TASK_TYPE
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class OwnerHandoffTask:
    task_type = TASK_TYPE

    def __init__(self, service: OwnerHandoffServiceProtocol):
        self._service = service

    def handle(self, payload):
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("operation_id"), str)
            or not isinstance(payload.get("avernet_tenant"), str)
        ):
            return Fail("INVALID_HANDOFF_TASK")
        with avernet_tenant_scope(payload["avernet_tenant"]):
            try:
                return (
                    Complete()
                    if self._service.step(payload["operation_id"])
                    else Reschedule(15)
                )
            except Exception:
                return Retry("HANDOFF_STORAGE_ERROR")


class OwnerHandoffLifecycle(LifecycleBase):
    def __init__(
        self,
        *,
        registry: HandlerRegistry,
        service: OwnerHandoffServiceProtocol,
        policy: OwnerHandoffPolicy,
    ):
        self._registry, self._service, self._policy = registry, service, policy
        self._loop_task = None

    async def bootstrap(self):
        self._registry.register(OwnerHandoffTask(self._service), wake_on_enqueue=True)

    async def startup(self):
        if self._policy.recovery_enabled:
            # Reconcile the journal/queue dual-write gap on boot and periodically.
            await asyncio.to_thread(self._service.reconcile)
            self._loop_task = asyncio.create_task(self._reconcile_loop())

    async def _reconcile_loop(self):
        while True:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(self._service.reconcile)
            except Exception:
                # Do not expose provider exception strings. The next scan retries
                # the durable journal; storage failure is never marked success.
                logger.warning(
                    "owner_handoff_reconciliation_failed: HANDOFF_STORAGE_ERROR"
                )

    async def shutdown(self):
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
