"""
Fallback Observability - 降级可观测

统一管理系统的降级日志和指标，确保所有 fallback 行为都能被观测到。

核心原则：
1. 没有 silent fallback
2. 每次 fallback 都有结构化日志
3. 关键 fallback 有 metrics 计数

使用方式：
    from src.infra.observability.fallback_logger import FallbackLogger

    logger = FallbackLogger()

    # 记录 embedding fallback
    logger.log_fallback(
        fallback_type="embedding_unavailable",
        reason="Real embedding API timeout",
        affected_component="vector_match_service",
        request_id="req-123",
        group_id="grp-456",
        provider_name="real_provider",
        model_name="Qwen3-Embedding-8B",
        latency_ms=5000,
    )

Fallback Types:
- llm_unavailable: LLM 服务不可用
- llm_fallback: LLM fallback 到规则生成
- embedding_unavailable: Embedding 服务不可用
- vector_match_to_keyword: 向量匹配降级到关键词检索
- registry_filter_disabled: Registry 过滤被禁用
- availability_warning_disabled: 可用性警告被禁用
- g5_to_basic: G5 专家诊断降级到基础处理
- feature_flag_disabled: Feature flag 关闭导致降级
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FallbackEvent:
    """
    Fallback 事件

    Attributes:
        fallback_type: 降级类型
        reason: 降级原因
        affected_component: 受影响的组件
        request_id: 请求 ID（可选）
        group_id: Group ID（可选）
        provider_name: Provider 名称（可选）
        model_name: 模型名称（可选）
        latency_ms: 延迟毫秒（可选）
        timestamp: 时间戳
        severity: 严重程度（info/warning/error）
    """
    fallback_type: str
    reason: str
    affected_component: str
    request_id: Optional[str] = None
    group_id: Optional[str] = None
    provider_name: Optional[str] = None
    model_name: Optional[str] = None
    latency_ms: Optional[int] = None
    timestamp: datetime = None
    severity: str = "warning"

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class FallbackLogger:
    """
    Fallback 日志记录器

    统一记录系统的降级行为，确保可观测性。

    使用示例：
        fallback_logger = FallbackLogger()

        # 记录 embedding fallback
        fallback_logger.log_fallback(
            fallback_type="embedding_unavailable",
            reason="Real embedding API timeout",
            affected_component="vector_match_service",
            provider_name="real_provider",
            model_name="Qwen3-Embedding-8B",
            latency_ms=5000,
        )
    """

    # Fallback 类型定义
    TYPES = {
        "llm_unavailable": "LLM 服务不可用",
        "llm_fallback": "LLM fallback 到规则生成",
        "embedding_unavailable": "Embedding 服务不可用",
        "embedding_fallback": "Embedding fallback 到关键词检索",
        "vector_match_to_keyword": "向量匹配降级到关键词检索",
        "registry_filter_disabled": "Registry 过滤被禁用",
        "availability_warning_disabled": "可用性警告被禁用",
        "g5_to_basic": "G5 专家诊断降级到基础处理",
        "feature_flag_disabled": "Feature flag 关闭导致降级",
        "keyword_only_recommendation": "关键词检索模式（无向量匹配）",
    }

    def __init__(self):
        """初始化"""
        self._counters: dict[str, int] = {}

    def log_fallback(
        self,
        fallback_type: str,
        reason: str,
        affected_component: str,
        request_id: Optional[str] = None,
        group_id: Optional[str] = None,
        provider_name: Optional[str] = None,
        model_name: Optional[str] = None,
        latency_ms: Optional[int] = None,
        severity: str = "warning",
    ) -> None:
        """
        记录 fallback 事件

        Args:
            fallback_type: 降级类型
            reason: 降级原因
            affected_component: 受影响的组件
            request_id: 请求 ID（可选）
            group_id: Group ID（可选）
            provider_name: Provider 名称（可选）
            model_name: 模型名称（可选）
            latency_ms: 延迟毫秒（可选）
            severity: 严重程度（info/warning/error）
        """
        # 创建事件
        event = FallbackEvent(
            fallback_type=fallback_type,
            reason=reason,
            affected_component=affected_component,
            request_id=request_id,
            group_id=group_id,
            provider_name=provider_name,
            model_name=model_name,
            latency_ms=latency_ms,
            severity=severity,
        )

        # 更新计数器
        self._increment_counter(fallback_type)

        # 构建结构化日志
        log_message = (
            f"⚠️ FALLBACK: [{event.fallback_type}] "
            f"component={event.affected_component} "
            f"reason={event.reason}"
        )

        # 添加可选字段
        extra_fields = {}
        if request_id:
            extra_fields["request_id"] = request_id
            log_message += f" request_id={request_id}"
        if group_id:
            extra_fields["group_id"] = group_id
            log_message += f" group_id={group_id}"
        if provider_name:
            extra_fields["provider_name"] = provider_name
            log_message += f" provider={provider_name}"
        if model_name:
            extra_fields["model_name"] = model_name
            log_message += f" model={model_name}"
        if latency_ms is not None:
            extra_fields["latency_ms"] = latency_ms
            log_message += f" latency={latency_ms}ms"

        # 添加结构化字段
        extra_fields.update({
            "fallback_type": event.fallback_type,
            "reason": event.reason,
            "affected_component": event.affected_component,
            "timestamp": event.timestamp.isoformat(),
            "severity": event.severity,
        })

        # 根据严重程度选择日志级别
        if severity == "error":
            logger.error(log_message, extra=extra_fields)
        elif severity == "warning":
            logger.warning(log_message, extra=extra_fields)
        else:
            logger.info(log_message, extra=extra_fields)

    def _increment_counter(self, fallback_type: str) -> None:
        """增加计数器"""
        if fallback_type not in self._counters:
            self._counters[fallback_type] = 0
        self._counters[fallback_type] += 1

    def get_counters(self) -> dict[str, int]:
        """
        获取所有计数器

        Returns:
            dict[str, int]: 计数器快照
        """
        return dict(self._counters)

    def reset_counters(self) -> None:
        """重置计数器（用于测试）"""
        self._counters.clear()
        logger.info("🔧 Fallback counters reset")


class FallbackMetrics:
    """
    Fallback Metrics 管理器

    提供简单的 metrics 计数功能。

    注意：这是一个最小实现，生产环境应该接入 Prometheus/Grafana 等监控系统。
    """

    def __init__(self):
        """初始化"""
        self._metrics: dict[str, int] = {
            "embedding_fallback_count": 0,
            "embedding_error_count": 0,
            "registry_filter_fallback_count": 0,
            "availability_warning_count": 0,
            "g5_fallback_count": 0,
            "vector_fallback_count": 0,
        }

    def increment(self, metric_name: str, value: int = 1) -> None:
        """
        增加指标计数

        Args:
            metric_name: 指标名称
            value: 增加的值（默认 1）
        """
        if metric_name in self._metrics:
            self._metrics[metric_name] += value
        else:
            logger.warning(f"Unknown metric: {metric_name}")

    def get_metrics(self) -> dict[str, int]:
        """
        获取所有指标

        Returns:
            dict[str, int]: 指标快照
        """
        return dict(self._metrics)

    def reset(self) -> None:
        """重置所有指标（用于测试）"""
        for key in self._metrics:
            self._metrics[key] = 0
        logger.info("🔧 Fallback metrics reset")


# 全局单例
_fallback_logger: Optional[FallbackLogger] = None
_fallback_metrics: Optional[FallbackMetrics] = None


def get_fallback_logger() -> FallbackLogger:
    """获取全局 FallbackLogger 实例"""
    global _fallback_logger
    if _fallback_logger is None:
        _fallback_logger = FallbackLogger()
    return _fallback_logger


def get_fallback_metrics() -> FallbackMetrics:
    """获取全局 FallbackMetrics 实例"""
    global _fallback_metrics
    if _fallback_metrics is None:
        _fallback_metrics = FallbackMetrics()
    return _fallback_metrics


__all__ = [
    "FallbackLogger",
    "FallbackMetrics",
    "FallbackEvent",
    "get_fallback_logger",
    "get_fallback_metrics",
]