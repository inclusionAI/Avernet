"""Privileged, disable-only Service API for published service-Bot cron jobs."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CronAdminServiceProtocol(Protocol):
    async def list_tasks(
        self, *, operator_id: str, bot_id: str, runtime_stage: str,
    ) -> dict: ...

    async def disable_task(
        self, *, operator_id: str, bot_id: str, task_id: str,
        runtime_stage: str, reason: str,
    ) -> dict: ...
