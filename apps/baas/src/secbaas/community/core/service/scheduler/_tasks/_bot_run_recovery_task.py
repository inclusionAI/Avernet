"""Bounded durable BCN callback recovery task.

This task is intentionally callback-only: it never claims Bot work, invokes a
Bot executor, or rolls queue state back.  The queue recovery worker is drained
separately during rollout; this sweep only scans terminal result rows and calls
the explicitly injected ``bcn_uplink`` callback.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.logger import get_logger

log = get_logger("core-scheduler")


@dataclass
class BotRunRecoveryTaskConfig:
    enabled: bool = True
    lock_name: str = "bot_run_recovery_lock"
    lock_expire_seconds: int = 120
    cron_interval_seconds: int = 60
    stale_seconds: int = 120  # retained for config compatibility; not used here
    dry_run: bool = False
    callback_batch_size: int | None = 2
    callback_max_attempts: int | None = 5
    callback_lease_seconds: int | None = 45
    callback_budget_seconds: float | None = 35.0

    def __post_init__(self) -> None:
        # Older overlays and minimal test containers do not carry the durable
        # callback keys. A dependency-injector configuration proxy resolves
        # missing paths to None, which must retain the code-level defaults
        # rather than reaching the validation casts below.
        if self.callback_batch_size is None:
            self.callback_batch_size = 2
        if self.callback_max_attempts is None:
            self.callback_max_attempts = 5
        if self.callback_lease_seconds is None:
            self.callback_lease_seconds = 45
        if self.callback_budget_seconds is None:
            self.callback_budget_seconds = 35.0

        if not 1 <= int(self.callback_batch_size) <= 2:
            raise ValueError("callback_batch_size must be between 1 and 2")
        if int(self.callback_max_attempts) <= 0:
            raise ValueError("callback_max_attempts must be positive")
        if int(self.callback_lease_seconds) <= 0:
            raise ValueError("callback_lease_seconds must be positive")
        if float(self.callback_budget_seconds) <= 0:
            raise ValueError("callback_budget_seconds must be positive")


class BotRunRecoveryTask:
    """Recover only durable BCN final callbacks under a bounded lease."""

    def __init__(
        self,
        config: BotRunRecoveryTaskConfig,
        lock_service: DistributedLockService,
        bot_run_repository: BotRunRepository | None = None,
        bcn_uplink_callback: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._config = config
        self._lock_service = lock_service
        self._bot_run_repository = bot_run_repository
        self._bcn_uplink_callback = bcn_uplink_callback

    @property
    def name(self) -> str:
        return "bot_run_recovery"

    @property
    def interval_seconds(self) -> int:
        return self._config.cron_interval_seconds

    async def run(self) -> None:
        start_time = time.monotonic()
        log.info("[BotRunRecovery] Task triggered at %s", datetime.now())
        if not self._config.enabled or self._config.dry_run:
            return
        if self._bot_run_repository is None or self._bcn_uplink_callback is None:
            log.warning("[BotRunRecovery] callback recovery dependencies unavailable")
            return

        with self._lock_service.try_lock(
            lock_name=self._config.lock_name,
            expire_seconds=self._config.lock_expire_seconds,
            block=False,
        ) as lock:
            if not lock.acquired:
                log.info("[BotRunRecovery] lock not acquired, skipping")
                return

            rows = self._bot_run_repository.scan_bcn_callback_pending(
                now=datetime.now(), limit=self._config.callback_batch_size
            )
            processed = 0
            for row in rows:
                if not self._lock_service.is_lock_held(
                    self._config.lock_name, lock.lock_holder, verify_remote=True
                ):
                    log.warning("[BotRunRecovery] lock lost, stopping callback sweep")
                    break
                try:
                    await asyncio.wait_for(
                        self._bcn_uplink_callback(row.run_id),
                        timeout=self._config.callback_budget_seconds,
                    )
                    processed += 1
                except TimeoutError:
                    log.warning(
                        "[BotRunRecovery] callback budget exhausted run_id=%s",
                        row.run_id,
                    )
                    break
                except Exception:
                    # One row must not prevent the next bounded row from being
                    # attempted; callback state remains durable and fenced.
                    log.warning(
                        "[BotRunRecovery] callback invocation failed run_id=%s",
                        row.run_id,
                    )

            log.info(
                "[BotRunRecovery] Completed: callbacks=%s scanned=%s duration=%.2fs",
                processed,
                len(rows),
                time.monotonic() - start_time,
            )
