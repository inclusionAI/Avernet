"""System health check response schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class ServiceHealth:
    healthy: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    is_master: Optional[bool] = None  # BCS only


@dataclass
class FeatureAvailability:
    available: bool
    reason: Optional[str] = None


@dataclass
class SystemHealthResponse:
    status: str  # "healthy" | "degraded" | "down"
    checked_at: str
    services: dict[str, ServiceHealth] = field(default_factory=dict)
    features: dict[str, FeatureAvailability] = field(default_factory=dict)


# ── Bot readiness (per-bot runtime state for frontend) ──────────────────────

ReadinessState = Literal[
    "starting",            # engine still booting / pre-init
    "ready",               # engine + underlying subprocess up
    "engine_unavailable",  # engine class up, underlying subprocess down (user-fixable)
    "failed",              # engine class init itself failed (not user-fixable)
    "unhealthy",           # adapter unreachable past grace window
    "unknown",             # transport failed within grace window (could be transient)
]


@dataclass
class BotReadiness:
    bot_id: str
    device_id: str
    state: ReadinessState
    reason: Optional[str] = None
    engine: Optional[str] = None
    version: Optional[str] = None
    age_seconds: Optional[int] = None
    checked_at: Optional[str] = None


@dataclass
class ReadinessResponse:
    checked_at: str
    mode: str
    total: int
    bots: list[BotReadiness] = field(default_factory=list)
