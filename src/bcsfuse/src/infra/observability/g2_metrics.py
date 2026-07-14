"""
G2 Conflict Analysis Metrics - G2冲突分析监控指标

按照 G2_LLM_CONFLICT_ANALYSIS_DESIGN.md 第10章监控与告警规范实现。

监控指标:
- g2_conflict_analysis_total: 各层分析调用次数
- g2_conflict_analysis_latency_ms: 各层分析延迟
- g2_conflict_severity_distribution: 冲突严重程度分布
- g2_conflict_fallback_total: Fallback次数统计

使用方式:
    from src.infra.observability.g2_metrics import G2Metrics

    metrics = G2Metrics()

    # 记录Layer1 LLM分析
    metrics.record_analysis(layer="llm", status="success", latency_ms=1500)
    metrics.record_fallback(from_layer="llm", to_layer="v2")

    # 获取摘要
    summary = metrics.get_summary()
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class G2LayerStats:
    """单层分析统计"""
    total_calls: int = 0
    success_count: int = 0
    failed_count: int = 0
    total_latency_ms: int = 0
    max_latency_ms: int = 0

    def record(self, success: bool, latency_ms: int) -> None:
        """记录一次调用"""
        self.total_calls += 1
        if success:
            self.success_count += 1
        else:
            self.failed_count += 1
        self.total_latency_ms += latency_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)

    @property
    def avg_latency_ms(self) -> float:
        """平均延迟"""
        return self.total_latency_ms / self.total_calls if self.total_calls > 0 else 0.0

    @property
    def success_rate(self) -> float:
        """成功率"""
        return self.success_count / self.total_calls if self.total_calls > 0 else 0.0


@dataclass
class G2Metrics:
    """
    G2冲突分析监控指标

    按照设计文档第10章规范实现。

    Attributes:
        layer_stats: 各层分析统计
        severity_counts: 严重程度分布
        fallback_counts: Fallback次数统计
        total_analyses: 总分析次数
    """

    layer_stats: Dict[str, G2LayerStats] = field(default_factory=lambda: {
        "llm": G2LayerStats(),
        "v2": G2LayerStats(),
        "legacy": G2LayerStats(),
    })

    severity_counts: Dict[str, int] = field(default_factory=lambda: {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "none": 0,
    })

    fallback_counts: Dict[str, int] = field(default_factory=lambda: {
        "llm_to_v2": 0,
        "v2_to_legacy": 0,
        "llm_to_legacy": 0,
    })

    total_analyses: int = 0
    final_source_counts: Dict[str, int] = field(default_factory=lambda: {
        "llm": 0,
        "v2": 0,
        "legacy": 0,
    })

    def record_analysis(
        self,
        layer: Literal["llm", "v2", "legacy"],
        status: Literal["success", "failed"],
        latency_ms: int,
    ) -> None:
        """
        记录一次分析调用

        Args:
            layer: 分析层（llm/v2/legacy）
            status: 状态（success/failed）
            latency_ms: 延迟毫秒数
        """
        if layer not in self.layer_stats:
            logger.warning(f"Unknown layer: {layer}")
            return

        success = status == "success"
        self.layer_stats[layer].record(success, latency_ms)

        log_level = logging.INFO if success else logging.WARNING
        logger.log(
            log_level,
            f"[G2-METRICS] Layer={layer}, status={status}, latency={latency_ms}ms"
        )

    def record_fallback(
        self,
        from_layer: Literal["llm", "v2"],
        to_layer: Literal["v2", "legacy"],
        reason: Optional[str] = None,
    ) -> None:
        """
        记录一次Fallback

        Args:
            from_layer: 来源层
            to_layer: 目标层
            reason: Fallback原因
        """
        key = f"{from_layer}_to_{to_layer}"
        if key in self.fallback_counts:
            self.fallback_counts[key] += 1
        else:
            logger.warning(f"Unknown fallback path: {key}")
            return

        logger.info(
            f"[G2-METRICS] Fallback: {from_layer} -> {to_layer}, reason={reason or 'unknown'}"
        )

    def record_severity(self, severity: str) -> None:
        """
        记录冲突严重程度

        Args:
            severity: 严重程度（critical/high/medium/low/none）
        """
        if severity in self.severity_counts:
            self.severity_counts[severity] += 1
        else:
            logger.warning(f"Unknown severity: {severity}")

    def record_final_result(
        self,
        source: Literal["llm", "v2", "legacy"],
        severity: str,
    ) -> None:
        """
        记录最终分析结果

        Args:
            source: 最终使用的分析层
            severity: 最终严重程度
        """
        self.total_analyses += 1
        if source in self.final_source_counts:
            self.final_source_counts[source] += 1
        self.record_severity(severity)

        logger.info(
            f"[G2-METRICS] Final result: source={source}, severity={severity}, "
            f"total_analyses={self.total_analyses}"
        )

    @contextmanager
    def track_analysis(
        self,
        layer: Literal["llm", "v2", "legacy"],
    ):
        """
        追踪分析耗时的上下文管理器

        Usage:
            with metrics.track_analysis("llm") as tracker:
                result = llm_analyzer.analyze(...)
                tracker.success = result is not None
        """
        start_time = time.time()
        tracker = {"success": True}

        try:
            yield tracker
        finally:
            latency_ms = int((time.time() - start_time) * 1000)
            status = "success" if tracker.get("success", True) else "failed"
            self.record_analysis(layer, status, latency_ms)

    def get_summary(self) -> Dict:
        """
        获取指标摘要

        Returns:
            Dict: 包含所有指标的摘要
        """
        return {
            "total_analyses": self.total_analyses,
            "layer_stats": {
                layer: {
                    "total_calls": stats.total_calls,
                    "success_count": stats.success_count,
                    "failed_count": stats.failed_count,
                    "success_rate": f"{stats.success_rate:.1%}",
                    "avg_latency_ms": f"{stats.avg_latency_ms:.1f}",
                    "max_latency_ms": stats.max_latency_ms,
                }
                for layer, stats in self.layer_stats.items()
            },
            "severity_distribution": dict(self.severity_counts),
            "fallback_counts": dict(self.fallback_counts),
            "final_source_distribution": dict(self.final_source_counts),
        }

    def print_summary(self) -> None:
        """打印指标摘要"""
        summary = self.get_summary()

        logger.info("=" * 70)
        logger.info("G2 Conflict Analysis Metrics Summary")
        logger.info("=" * 70)

        logger.info(f"Total Analyses: {summary['total_analyses']}")

        logger.info("-" * 40)
        logger.info("Layer Statistics:")
        for layer, stats in summary["layer_stats"].items():
            logger.info(
                f"  {layer.upper()}: calls={stats['total_calls']}, "
                f"success_rate={stats['success_rate']}, "
                f"avg_latency={stats['avg_latency_ms']}ms"
            )

        logger.info("-" * 40)
        logger.info("Severity Distribution:")
        for severity, count in summary["severity_distribution"].items():
            logger.info(f"  {severity}: {count}")

        logger.info("-" * 40)
        logger.info("Fallback Counts:")
        for path, count in summary["fallback_counts"].items():
            logger.info(f"  {path}: {count}")

        logger.info("-" * 40)
        logger.info("Final Source Distribution:")
        for source, count in summary["final_source_distribution"].items():
            logger.info(f"  {source}: {count}")

    def reset(self) -> None:
        """重置所有指标（用于测试）"""
        self.layer_stats = {
            "llm": G2LayerStats(),
            "v2": G2LayerStats(),
            "legacy": G2LayerStats(),
        }
        self.severity_counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "none": 0,
        }
        self.fallback_counts = {
            "llm_to_v2": 0,
            "v2_to_legacy": 0,
            "llm_to_legacy": 0,
        }
        self.total_analyses = 0
        self.final_source_counts = {
            "llm": 0,
            "v2": 0,
            "legacy": 0,
        }
        logger.info("[G2-METRICS] Metrics reset")


# 全局单例
_g2_metrics: Optional[G2Metrics] = None


def get_g2_metrics() -> G2Metrics:
    """获取全局 G2Metrics 实例"""
    global _g2_metrics
    if _g2_metrics is None:
        _g2_metrics = G2Metrics()
    return _g2_metrics


def reset_g2_metrics() -> None:
    """重置全局指标（用于测试）"""
    global _g2_metrics
    if _g2_metrics is not None:
        _g2_metrics.reset()


__all__ = [
    "G2Metrics",
    "G2LayerStats",
    "get_g2_metrics",
    "reset_g2_metrics",
]