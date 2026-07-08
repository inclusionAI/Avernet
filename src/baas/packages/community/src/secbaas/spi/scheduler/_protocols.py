"""Scheduler plugin Protocol — cron job scheduling contract."""

from __future__ import annotations

from typing import Protocol


class SchedulerPlugin(Protocol):
    """Plugin protocol for scheduled job execution.

    Implementations:
    - ApsSchedulerPlugin: APScheduler AsyncIOScheduler for production.
    - StubSchedulerPlugin: in-process asyncio loop for tests.
    """

    def start(self) -> None:
        """Start the scheduler — begins firing jobs on the configured interval."""
        ...

    def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        ...

    def trigger_now(self) -> None:
        """Fire the scheduled job immediately (manual trigger, outside schedule)."""
        ...
