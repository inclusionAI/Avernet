"""Scheduler SPI — shared configuration models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SchedulerConfig:
    """Generic scheduler configuration base."""

    enabled: bool = True
    cron_interval_seconds: int = 3600  # 1 hour
