"""BotRun 队列恢复定时 task

周期性地把"心跳过期"的 RUNNING 请求重置回 PENDING，
供其他 Worker 重新认领。

依赖通过构造方法注入，由 DI 容器管理生命周期。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from secbaas.core.repository.bot_run_queue import BotRunQueueRepository
from secbaas.core.service.distributed_lock import DistributedLockService
from secbaas.logger import get_logger

log = get_logger("core-scheduler")


@dataclass
class BotRunRecoveryTaskConfig:
    """BotRun 队列恢复 task 配置"""

    enabled: bool = True
    lock_name: str = "bot_run_recovery_lock"
    lock_expire_seconds: int = 120
    cron_interval_seconds: int = 60
    stale_seconds: int = 120
    dry_run: bool = False


class BotRunRecoveryTask:
    """BotRun 队列恢复 task

    抢分布式锁 → 重置悬挂 RUNNING → 释放锁。
    """

    def __init__(
        self,
        config: BotRunRecoveryTaskConfig,
        lock_service: DistributedLockService,
        queue_repo: BotRunQueueRepository,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._queue_repo = queue_repo

    @property
    def name(self) -> str:
        return "bot_run_recovery"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self) -> None:
        start_time = time.monotonic()
        log.info("[BotRunRecovery] Task triggered at %s", datetime.now())

        if not self._config.enabled:
            log.info("[BotRunRecovery] Disabled, skipping")
            return

        if self._config.dry_run:
            log.info("[BotRunRecovery] DRY_RUN mode - skipping")
            return

        with self._lock_service.try_lock(
            lock_name=self._config.lock_name,
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                log.info(
                    "[BotRunRecovery] Lock %s not acquired, skipping",
                    self._config.lock_name,
                )
                return

            try:
                reset = self._queue_repo.reset_stale_running(self._config.stale_seconds)
            except Exception:
                log.exception("[BotRunRecovery] Error resetting stale RUNNING")
                raise

            duration = time.monotonic() - start_time
            log.info(
                "[BotRunRecovery] Completed: reset=%s stale_seconds=%s duration=%.2fs",
                reset,
                self._config.stale_seconds,
                duration,
            )
