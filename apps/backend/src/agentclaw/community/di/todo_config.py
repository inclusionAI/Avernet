"""Typed configuration for the Todo automatic scheduler."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TodoSchedulerConfig:
    enabled: bool = True
    interval: int = 60
    batch_size: int = 50
    jitter: int = 5
    lock_ttl: int = 240
    heartbeat: int = 45


__all__ = ["TodoSchedulerConfig"]
