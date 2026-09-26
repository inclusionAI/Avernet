"""Dormant bot lifecycle — single scan cron with leader-election lock.

Runs ONE background daemon thread:
  * scan thread — fires at 03:00 daily, runs DormantBotService.process_run

Notification SENDING is no longer this module's concern: the container-side
notify-sender bot pulls pending rows from /api/internal/dormant/pending-notifications
on its own schedule (typically 09:00).

DormantBotLifecycle extends LifecycleBase so it is auto-discovered by
discover_lifecycle_participants() when the DI module binds it as a singleton.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from datetime import time as dtime

from injector import inject

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.core.bot_dormant.scan_policy import DormantScanPolicyService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_SCAN_LOCK_KEY = "bot_dormant_scan_lock"
_LOCK_TTL = 1800  # 30 min — upper bound for a single scan round
_SCAN_HOUR = 3    # 03:00 daily


class DormantBotLifecycle(LifecycleBase):
    """Lifecycle participant that drives the dormant-bot scan cron loop."""

    @inject
    def __init__(
        self,
        service: DormantBotService,
        cache: CachePlugin,
        scan_policy: DormantScanPolicyService,
    ) -> None:
        self._service = service
        self._cache = cache
        self._scan_policy = scan_policy
        self._stop_event = threading.Event()
        self._scan_thread: threading.Thread | None = None
        env = get_current_env()
        self._env = env
        self._scan_lock = f"{_SCAN_LOCK_KEY}:{env}"

    async def startup(self) -> None:
        self._stop_event.clear()
        self._scan_thread = threading.Thread(
            target=self._loop, args=(_SCAN_HOUR, self._run_scan), daemon=True,
        )
        self._scan_thread.start()
        logger.info("[dormant] lifecycle started: scan@03:00 env=%s", self._env)

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=5)

    def _loop(self, target_hour: int, runner) -> None:
        while not self._stop_event.is_set():
            try:
                wait_seconds = self._seconds_until(target_hour)
                if self._stop_event.wait(timeout=wait_seconds):
                    break
                runner()
            except Exception:
                logger.exception("[dormant] loop error")
                self._stop_event.wait(timeout=60)

    def _seconds_until(self, target_hour: int) -> float:
        now = datetime.now()
        target = datetime.combine(now.date(), dtime(hour=target_hour, minute=0))
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _run_scan(self) -> None:
        policy = self._scan_policy.get_policy()
        if not policy.scheduled_scan_enabled:
            logger.info(
                "[dormant] scheduled scan disabled by policy env=%s source=%s dry_run=%s",
                policy.env,
                policy.source,
                policy.dry_run,
            )
            return

        token = self._cache.acquire_lock(self._scan_lock, ttl=_LOCK_TTL)
        if not token:
            logger.info("[dormant] scan lock not acquired, skip")
            return
        try:
            asyncio.run(
                self._service.process_run(dry_run=policy.dry_run)
            )
        finally:
            self._cache.release_lock(self._scan_lock, token)
