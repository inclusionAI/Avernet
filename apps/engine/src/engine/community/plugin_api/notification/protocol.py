from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.plugin_api.cron.models import CronJob, CronRunRecord


@runtime_checkable
class NotificationService(Protocol):
    """Profile-specific notification boundary."""

    async def send_cron_notification(
        self, job: CronJob, run: CronRunRecord, timeout_secs: float = 10.0
    ) -> bool: ...
