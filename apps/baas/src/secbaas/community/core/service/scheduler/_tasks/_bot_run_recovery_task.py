"""BotRun 队列恢复定时 task

周期性地把"心跳过期"的 RUNNING 请求重置回 PENDING，
供其他 Worker 重新认领。同时在抢到 ``bot_run_recovery_lock`` 的实例上
对账清理 ``ac_lock_table`` 中过期的 ``botrun:session:`` 孤儿锁，
不依赖锁 TTL（约 3h）自愈。

依赖通过构造方法注入，由 DI 容器管理生命周期。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from secbaas.community.core.repository.bot_run_queue import BotRunQueueRepository
from secbaas.community.core.repository.distributed_lock import (
    DistributedLockRepository,
)
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.logger import get_logger

log = get_logger("core-scheduler")

#: ``ac_lock_table`` 中 session 串行锁的命名前缀，与
#: ``SerializingExecutor`` 的 ``DEFAULT_SESSION_LOCK_PREFIX`` 保持一致。
DEFAULT_SESSION_LOCK_PREFIX = "botrun:session:"


@dataclass
class BotRunRecoveryTaskConfig:
    """BotRun 队列恢复 task 配置"""

    enabled: bool = True
    lock_name: str = "bot_run_recovery_lock"
    lock_expire_seconds: int = 120
    cron_interval_seconds: int = 60
    stale_seconds: int = 120
    dry_run: bool = False
    #: 对账清理的 session 锁前缀；与 SerializingExecutor 的锁命名保持一致。
    session_lock_prefix: str = DEFAULT_SESSION_LOCK_PREFIX


class BotRunRecoveryTask:
    """BotRun 队列恢复 task

    抢分布式锁 → 重置悬挂 RUNNING → 对账清理过期孤儿 session 锁 → 释放锁。
    """

    def __init__(
        self,
        config: BotRunRecoveryTaskConfig,
        lock_service: DistributedLockService,
        queue_repo: BotRunQueueRepository,
        lock_repo: DistributedLockRepository | None = None,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._queue_repo = queue_repo
        self._lock_repo = lock_repo

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

            # 对账：清理 ``ac_lock_table`` 中过期的 ``botrun:session:`` 孤儿锁。
            # 失败仅 log，不阻塞主流程（reset 已完成）。
            reaped = self._reap_orphan_session_locks()

            duration = time.monotonic() - start_time
            log.info(
                "[BotRunRecovery] Completed: reset=%s reaped=%s "
                "stale_seconds=%s duration=%.2fs",
                reset,
                reaped,
                self._config.stale_seconds,
                duration,
            )

    def _reap_orphan_session_locks(self) -> int:
        """清理过期孤儿 session 锁，返回被删除的行数；失败仅记录日志。

        - 未注入 ``lock_repo`` 时跳过（兼容不依赖该对账的既有装配/测试）。
        - 限定 ``session_lock_prefix``，仅清理已过期（``expire_time <= now``）
          的 ``botrun:session:`` 锁；正常持有的锁由续期线程保持不会过期。
        """
        if self._lock_repo is None:
            return 0
        try:
            now = datetime.now()
            deleted = self._lock_repo.delete_expired_locks_by_prefix(
                self._config.session_lock_prefix, now=now
            )
        except Exception:
            log.exception(
                "[BotRunRecovery] Error reaping orphan session locks (prefix=%s)",
                self._config.session_lock_prefix,
            )
            return 0
        if deleted:
            log.info(
                "[BotRunRecovery] reaped %s expired session locks (prefix=%s)",
                deleted,
                self._config.session_lock_prefix,
            )
        return deleted
