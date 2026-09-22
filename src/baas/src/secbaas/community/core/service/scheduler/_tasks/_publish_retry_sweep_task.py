"""Publish retry sweep task.

Detects in-flight device publish records that have outrun their attempt
window and routes them back through the publish service orchestrator, so
timeout-driven retries advance without a client polling for progress.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.publish_record import (
        PublishRecordRepository,
    )
    from secbaas.community.core.service.publish_manage import PublishService

log = get_logger("core-scheduler")


@dataclass
class PublishRetrySweepConfig:
    enabled: bool = True
    lock_name: str = "publish_retry_sweep_lock"
    lock_expire_seconds: int = 240
    cron_interval_seconds: int = 60
    attempt_timeout_seconds: int = 1800
    batch_limit: int = 100
    dry_run: bool = False


class PublishRetrySweepTask:
    """Advance timed-out publish attempts via the publish service orchestrator."""

    def __init__(
        self,
        config: PublishRetrySweepConfig,
        lock_service: DistributedLockService,
        record_repo: PublishRecordRepository,
        publish_service: PublishService,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._record_repo = record_repo
        self._publish_service = publish_service

    @property
    def name(self) -> str:
        return "publish_retry_sweep"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self) -> None:
        start_time = time.monotonic()

        if not self._config.enabled:
            log.info("[PublishRetrySweep] Disabled, skipping")
            return
        if self._config.dry_run:
            log.info("[PublishRetrySweep] DRY_RUN mode - skipping")
            return

        env = get_current_env()

        with self._lock_service.try_lock(
            lock_name=self._config.lock_name,
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                log.info(
                    "[PublishRetrySweep] Lock %s not acquired, skipping",
                    self._config.lock_name,
                )
                return

            stale = self._record_repo.list_stale_processing_records_across_tenants(
                timeout_seconds=self._config.attempt_timeout_seconds,
                env=env,
            )
            if not stale:
                log.info("[PublishRetrySweep] No stale in-flight records")
                return

            log.warning(
                "[PublishRetrySweep] Found %s stale in-flight records",
                len(stale),
            )

            retried = 0
            settled = 0
            for record in stale[: self._config.batch_limit]:
                try:
                    started = await self._publish_service.sweep_record_attempt(
                        record=record, tenant=record.tenant
                    )
                except Exception:
                    log.exception(
                        "[PublishRetrySweep] Failed to sweep record=%s",
                        record.id,
                    )
                    continue
                if started:
                    retried += 1
                else:
                    settled += 1

            log.info(
                "[PublishRetrySweep] Completed: retried=%s settled=%s "
                "stale=%s duration=%.2fs",
                retried,
                settled,
                len(stale),
                time.monotonic() - start_time,
            )
