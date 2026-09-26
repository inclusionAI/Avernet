from __future__ import annotations

import asyncio
import logging

from engine.community.plugin_api.cron.models import CronJob, CronRunRecord

log = logging.getLogger("engine.notification")


class LoggerNotificationService:
    """Community notification implementation: log and report success."""

    async def send_cron_notification(
        self, job: CronJob, run: CronRunRecord, timeout_secs: float = 10.0
    ) -> bool:
        try:
            return await asyncio.wait_for(self._send(job, run), timeout=timeout_secs)
        except asyncio.TimeoutError:
            log.error("[cron_notify] send timeout: job=%s timeout=%s", job.name, timeout_secs)
            return False

    async def _send(self, job: CronJob, run: CronRunRecord) -> bool:
        log.info(
            "[cron_notify] job=%s status=%s duration_ms=%s output=%s error=%s",
            job.name,
            run.status,
            run.duration_ms,
            (run.output or "")[:500],
            (run.error or "")[:500],
        )
        return True
