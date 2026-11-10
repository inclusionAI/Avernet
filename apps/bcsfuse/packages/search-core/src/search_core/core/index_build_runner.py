"""Lifespan-owned, on-demand execution; no idle build-table polling."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class IndexBuildRunner:
    def __init__(self, service):
        self.service = service
        self._wake = asyncio.Event()
        self._pending = set()
        self._active = ""

    def trigger(self, build_id: str):
        """Called on the application's event loop after the task is committed."""
        if build_id != self._active:
            self._pending.add(build_id)
            self._wake.set()

    async def run(self, stop):
        while not stop.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except TimeoutError:
                continue
            self._wake.clear()
            while self._pending and not stop.is_set():
                self._active = self._pending.pop()
                try:
                    await self._run_requested(self._active, stop)
                except Exception:  # noqa: BLE001 -- durable state remains available for explicit retry
                    logger.warning(
                        "Index build execution unavailable; resubmit the build ID"
                    )
                finally:
                    self._active = ""

    async def _run_requested(self, build_id, stop):
        while not stop.is_set():
            await asyncio.to_thread(self.service.run_once, stop, build_id=build_id)
            row = await asyncio.to_thread(self.service.status, build_id)
            if row.status not in {"pending", "running"}:
                return
            # Only an explicitly requested task waits for a competing lease or
            # source-revision conflict. Failed tasks require another HTTP PUT.
            try:
                await asyncio.wait_for(stop.wait(), 5.0)
            except TimeoutError:
                pass
