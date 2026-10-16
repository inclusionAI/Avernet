"""Always-on redrive for durable Relay delivery intents."""

from __future__ import annotations

import asyncio
import logging
import threading

from injector import Injector, inject

from agentclaw.community.core.task.task_service_protocol import TaskServiceProtocol
from agentclaw.community.kernel.lifecycle import LifecycleBase

logger = logging.getLogger("task.delivery")


class TaskDeliveryLifecycle(LifecycleBase):
    """A crashed process leaves an intent for another instance to claim."""

    @inject
    def __init__(self, injector: Injector) -> None:
        self._injector = injector
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def startup(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="task-delivery", daemon=True)
        self._thread.start()

    async def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                service = self._injector.get(TaskServiceProtocol)
                delivered = asyncio.run(service.dispatch_pending_intents(limit=100))
                if delivered:
                    logger.info("redrove %d task delivery intents", delivered)
            except Exception:
                logger.exception("task delivery redrive failed")
            self._stop.wait(10)
