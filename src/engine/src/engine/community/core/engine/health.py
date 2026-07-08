"""
Engine health status — result type returned by Engine.health_check().

Kept minimal here. The full HealthPlugin with components, metrics, and history
will live under core/health/ when added (see
src/engine/docs/heterogeneous-engine-architecture.md §12 and Appendix B).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HealthStatus:
    """Snapshot of an engine's health.

    `healthy`: True if the engine is currently usable.
    `message`: Human-readable description (e.g. "OK", "process not running").
    `details`: Optional structured details for debugging.
    """

    healthy: bool
    message: str = "OK"
    details: dict[str, Any] | None = None


__all__ = ["HealthStatus"]
