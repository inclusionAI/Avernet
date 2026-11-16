"""Lifecycle for the per-instance index, retaining the existing TC schedule."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _create_scheduler() -> Any:
    from apscheduler.schedulers.background import BackgroundScheduler

    return BackgroundScheduler()


class VectorIndexLifecycleProvider:
    """Rebuild before readiness and coordinate incremental/full sync jobs."""

    def __init__(
        self,
        vector_store: Any,
        *,
        sync_interval_seconds: int = 60,
        scheduler_factory: Callable[[], Any] = _create_scheduler,
    ) -> None:
        if sync_interval_seconds <= 0:
            raise ValueError("sync_interval_seconds must be positive")
        self._vector_store = vector_store
        self._sync_interval_seconds = sync_interval_seconds
        self._scheduler_factory = scheduler_factory
        self._scheduler: Any | None = None
        self._initialized = False
        self._shutdown = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            self._vector_store.rebuild_from_backend()
            scheduler = self._scheduler_factory()
            scheduler.add_job(
                self._vector_store.sync_incremental,
                trigger="interval",
                seconds=self._sync_interval_seconds,
                id="bcsfuse_vector_incremental_sync",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.add_job(
                self._vector_store.rebuild_from_backend,
                trigger="cron",
                hour=4,
                minute=0,
                id="bcsfuse_vector_full_rebuild",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            try:
                scheduler.start()
            except Exception:
                try:
                    scheduler.shutdown(wait=True)
                except Exception:  # noqa: BLE001 - preserve startup failure
                    logger.warning("scheduler cleanup failed after start failure")
                raise
            self._scheduler = scheduler
            self._initialized = True

    async def shutdown(self) -> None:
        async with self._lock:
            if self._shutdown:
                return
            try:
                if self._scheduler is not None:
                    self._scheduler.shutdown(wait=True)
            finally:
                self._vector_store.close()
                self._shutdown = True


__all__ = ["VectorIndexLifecycleProvider"]
