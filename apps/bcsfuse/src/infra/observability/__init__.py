"""
Observability 模块

提供系统可观测性能力，包括 fallback 日志和 metrics。
"""

from src.infra.observability.fallback_logger import (
    FallbackLogger,
    FallbackMetrics,
    FallbackEvent,
    get_fallback_logger,
    get_fallback_metrics,
)

__all__ = [
    "FallbackLogger",
    "FallbackMetrics",
    "FallbackEvent",
    "get_fallback_logger",
    "get_fallback_metrics",
]