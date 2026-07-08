from __future__ import annotations

import pytest

from engine.community.plugin_api.cron.models import CronJob, CronRunRecord
from engine.community.plugins.notification.logger_impl import LoggerNotificationService


def _job() -> CronJob:
    return CronJob(id="j", name="job", schedule={}, payload={}, created_at_ms=1, updated_at_ms=1)


def _run() -> CronRunRecord:
    return CronRunRecord(job_id="j", started_at_ms=1, finished_at_ms=2, status="ok", duration_ms=1)


@pytest.mark.asyncio
async def test_logger_notification_returns_true():
    assert await LoggerNotificationService().send_cron_notification(_job(), _run()) is True
