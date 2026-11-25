"""Existing queue executes pointers; a bounded outbox scan dispatches pending heads."""

import asyncio

from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
    TASK_TYPE,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Retry
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()
POLL_INTERVAL_SECONDS = 30


class SpaceMembershipSyncTaskHandler:
    task_type = TASK_TYPE

    def __init__(self, synchronizer: SpaceMembershipSynchronizer, environment: str):
        self._sync, self._environment = synchronizer, environment

    def handle(self, payload):
        try:
            if (
                not isinstance(payload, dict)
                or type(payload.get("revision")) is not int
                or payload["revision"] < 1
            ):
                return Retry(error="invalid membership task")
            space_id = payload.get("_space_id")
            if type(space_id) is not int or space_id < 1:
                return Retry(error="invalid membership scope")
            if not self._sync.is_enabled():
                return Retry(error="membership delivery disabled")
            progress = self._sync.progress(
                space_id=space_id, environment=self._environment
            )
            if progress is None:
                return Retry(error="membership state missing")
            if progress[1] >= payload["revision"]:
                return Complete()
            if progress[1] + 1 != payload["revision"]:
                return Retry(error="membership predecessor pending")
            self._sync.deliver_next(space_id=space_id, environment=self._environment)
            return Complete()
        except Exception:
            # No raw HTTP/SQL/identity or complete member list in queue error records.
            return Retry(error="membership delivery pending")


class SpaceMembershipSyncLifecycle(LifecycleBase):
    def __init__(
        self,
        registry: HandlerRegistry,
        synchronizer: SpaceMembershipSynchronizer,
        environment: str,
    ):
        self._registry, self._sync, self._environment = (
            registry,
            synchronizer,
            environment,
        )
        self._task = None

    async def bootstrap(self):
        self._registry.register(
            SpaceMembershipSyncTaskHandler(self._sync, self._environment)
        )

    async def startup(self):
        if self._sync.is_enabled():
            self._task = asyncio.create_task(self._repair())

    async def _repair(self):
        cursor = 0
        while True:
            try:
                cursor = await asyncio.to_thread(
                    self._sync.repair_pending,
                    environment=self._environment,
                    after_space_id=cursor,
                )
            except Exception:
                logger.warning("membership repair pending; no payload logged")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def shutdown(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
