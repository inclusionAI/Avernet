"""
Cron Notify Module - 定时任务通知公共逻辑。

Internal notification vendors live under profile plugins. This module keeps only
profile-neutral helpers shared by notification plugins and cron polling.
"""

from __future__ import annotations

import logging
import os

from engine.community.plugin_api.cron.models import CronJob, CronRunRecord
from engine.community.plugin_api.notification.protocol import NotificationService

log = logging.getLogger("new_ocb.cron-notify")


def _get_default_user_ids() -> list[str]:
    """从环境变量或 credentials 文件读取默认 staff_id。"""
    if staff_id := os.getenv("STAFF_ID"):
        return [staff_id]

    try:
        path = os.getenv("CREDENTIALS_PATH", "/home/admin/.credentials")
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CLIENT_ID="):
                    value = line[10:]
                    parts = value.split("_")
                    if len(parts) >= 2 and parts[0] == "staff":
                        return [parts[1]]
    except Exception:
        pass

    return []


def _resolve_user_ids(job: CronJob) -> list[str]:
    """解析任务的通知目标用户 ID。"""
    if job.notify and job.notify.user_ids:
        return job.notify.user_ids

    default_users = _get_default_user_ids()
    if default_users:
        return default_users

    return []


def make_notify_callback(service: NotificationService):
    async def _callback(job: CronJob, run: CronRunRecord, timeout_secs: float = 10.0) -> bool:
        return await service.send_cron_notification(job, run, timeout_secs=timeout_secs)

    return _callback


async def send_cron_notification(
    service: NotificationService,
    job: CronJob,
    run: CronRunRecord,
    timeout_secs: float = 10.0,
) -> bool:
    """Send a cron notification through the injected notification service."""

    return await service.send_cron_notification(job, run, timeout_secs=timeout_secs)
