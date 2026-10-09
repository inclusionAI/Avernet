"""Governance bot lifecycle — scheduled daily scan + once-lock dedup.

Fires ``process_cron_tick`` daily at a configurable hour (default 14:00).
Each tick acquires a once-lock keyed by the current date via
``CachePlugin.acquire_lock`` (NX + 2-day TTL).  On any given day,
at most one process across the cluster wins the lock and runs
``process_cron_tick``; all others get ``None`` and silently skip.
The winner **never calls ``release_lock``** — the key persists
via TTL and its existence IS the proof that today's tick
has already run.

This combines two guarantees:
  - **Scheduled timing**: each Pod sleeps until the target hour,
    not polling every N minutes.
  - **Cross-Pod dedup**: the once-lock prevents duplicate execution
    even if multiple Pods wake up at the same time or the scan
    finishes quickly (lock is never released, so no re-acquisition).
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.services.scan_service import GovernanceBotService
    from agentclaw.community.core.economy.governance.services.service_protocols import (
        GovernanceAdminServiceProtocol,
    )
    from agentclaw.community.plugin_api.cache import CachePlugin

log = get_logger(__name__)

# TTL for the once-lock: 2 days — generous buffer over single-day scope.
_ONCE_LOCK_TTL_SECONDS = 2 * 86400


class GovernanceBotLifecycle(LifecycleBase):
    """Lifecycle participant — daily governance scan with once-lock.

    Auto-discovered by ``discover_lifecycle_participants()`` when
    the DI module binds this as a singleton.

    The once-lock guarantees that, on any given day, **at most one**
    process across the cluster runs ``process_cron_tick``.  All other
    processes / tick loops see the key already present and skip.
    The winner never releases the lock — the key persists via TTL
    (2 days) and its existence IS the proof that today's tick
    has already run.
    """

    def __init__(
        self,
        service: GovernanceBotService,
        cache: CachePlugin,
        config: Any,  # EconomyGovernanceConfig
        admin_svc: GovernanceAdminServiceProtocol,
    ) -> None:
        self._service = service
        self._cache = cache
        self._config = config
        self._admin_svc = admin_svc
        self._stop_event = threading.Event()
        self._scan_thread: threading.Thread | None = None
        env = get_current_env()
        self._scan_lock_prefix = f"once:governance_scan:{env}"

    async def startup(self) -> None:
        """Start the scan daemon thread."""
        self._stop_event.clear()
        target_hour = getattr(self._config, "scan_hour", 14)
        target_minute = getattr(self._config, "scan_minute", 0)
        self._scan_thread = threading.Thread(
            target=self._loop,
            args=(target_hour, target_minute),
            daemon=True,
        )
        self._scan_thread.start()
        log.info(
            "[GovernanceLifecycle] Started scan daemon "
            "(hour=%d, minute=%d)",
            target_hour, target_minute,
        )

    async def shutdown(self) -> None:
        """Signal the daemon thread to stop and wait."""
        self._stop_event.set()
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=5)
        log.info("[GovernanceLifecycle] Stopped scan daemon")

    def _loop(self, target_hour: int, target_minute: int) -> None:
        """Sleep-until-target-time loop.

        Calculates seconds until the next ``target_hour:target_minute``,
        sleeps (in 60 s chunks for clean shutdown), then fires the
        scan.  After execution the loop re-calculates for tomorrow.
        """
        while not self._stop_event.is_set():
            seconds = self._seconds_until(target_hour, target_minute)
            log.info(
                "[GovernanceLifecycle] Next scan in %d seconds "
                "(hour=%d, minute=%d)",
                seconds, target_hour, target_minute,
            )
            # Sleep in short intervals to allow early wake on shutdown
            while seconds > 0 and not self._stop_event.is_set():
                sleep_dur = min(seconds, 60)
                self._stop_event.wait(sleep_dur)
                seconds -= sleep_dur

            if self._stop_event.is_set():
                break

            try:
                self._run_scan()
            except Exception:
                log.exception("[GovernanceLifecycle] Scan run failed")

    def _run_scan(self) -> None:
        """Acquire once-lock for today, then run process_cron_tick().

        The once-lock is keyed by the current date (e.g.
        ``once:governance_scan:singlebox:20260705``) with a 2-day TTL.
        If another Pod already won today's lock, this Pod silently skips.
        The winner **never releases** the lock — its persistence
        via TTL prevents any Pod from re-acquiring on the same day,
        even if the scan finishes in under a second.
        """
        scan_date = datetime.now().strftime("%Y%m%d")
        lock_key = f"{self._scan_lock_prefix}:{scan_date}"

        token = self._cache.acquire_lock(lock_key, ttl=_ONCE_LOCK_TTL_SECONDS)
        if not token:
            log.info(
                "[GovernanceLifecycle] Lock %s not acquired — "
                "another Pod already ran today, skip",
                lock_key,
            )
            return

        log.info(
            "[GovernanceLifecycle] Lock acquired, starting scan. "
            "lock=%s token=%s",
            lock_key, token[:8],
        )

        # 制动判定(lock 之后):制动生效则跳过本次自动 tick。锁已抢下,
        # 当日执行权消耗——制动是风险信号,应尽快回退后次日恢复,当日不补跑。
        # 手动接口(trigger-scan / scan-and-deliver / tickets:deliver)不经此
        # 路径,制动不影响手动排障/补投(见 process_cron_tick docstring)。
        if self._admin_svc.is_paused():
            self._admin_svc.write_brake_skip_audit(
                run_id=scan_date,
                reason="scheduled tick skipped: governance brake active",
            )
            log.info(
                "[GovernanceLifecycle] Brake active — skip scheduled tick, "
                "date=%s (lock held, no rerun today)", scan_date,
            )
            return

        started = datetime.now()
        try:
            summary = self._service.process_cron_tick()
            duration = (datetime.now() - started).total_seconds()
            log.info(
                "[GovernanceLifecycle] Scan completed: run_id=%s, "
                "sent=%d, failed=%d, cancelled=%d, reminders=%d, "
                "schedule_due=%d, "
                "dry_run=%s, duration=%.1fs",
                summary.run_id,
                summary.sent_count,
                summary.failed_count,
                summary.cancelled_count,
                summary.reminders_created,
                summary.schedule_due_count,
                summary.dry_run,
                duration,
            )
        except Exception:
            log.exception("[GovernanceLifecycle] Scan run failed with exception")
        # Lock is NEVER released — it persists via TTL (2 days).
        # This is intentional: its existence prevents any Pod
        # from re-acquiring on the same day.

    @staticmethod
    def _seconds_until(target_hour: int, target_minute: int = 0) -> int:
        """Calculate seconds until the next target hour:minute."""
        now = time.localtime()
        target_sec = (
            (target_hour - now.tm_hour) * 3600
            + (target_minute - now.tm_min) * 60
            - now.tm_sec
        )
        if target_sec <= 0:
            target_sec += 24 * 3600  # Next day
        return target_sec
