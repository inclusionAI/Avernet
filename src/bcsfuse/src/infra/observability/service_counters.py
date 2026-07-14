"""
Service Call Counters - 服务调用计数器

记录系统关键服务的调用情况，用于真实性和可观测性证明。

计数器列表：
- llm_real_call_count: LLM 真实调用次数
- llm_fallback_count: LLM fallback 次数
- llm_error_count: LLM 错误次数
- embedding_real_call_count: Embedding 真实调用次数
- embedding_fallback_count: Embedding fallback 次数
- embedding_error_count: Embedding 错误次数
- vector_fallback_count: Vector match fallback 次数
- keyword_only_count: Keyword-only recommendation 次数

使用方式：
    from src.infra.observability.service_counters import ServiceCounters

    counters = ServiceCounters()

    # 记录 LLM 调用
    counters.increment_llm_real_call()
    counters.increment_llm_fallback()

    # 获取计数
    summary = counters.get_summary()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass
class ServiceCounters:
    """
    服务调用计数器

    记录系统关键服务的调用情况。

    Attributes:
        llm_real_call_count: LLM 真实调用次数
        llm_fallback_count: LLM fallback 次数
        llm_error_count: LLM 错误次数
        embedding_real_call_count: Embedding 真实调用次数
        embedding_fallback_count: Embedding fallback 次数
        embedding_error_count: Embedding 错误次数
        vector_fallback_count: Vector match fallback 次数
        keyword_only_count: Keyword-only recommendation 次数
    """

    llm_real_call_count: int = 0
    llm_fallback_count: int = 0
    llm_error_count: int = 0

    embedding_real_call_count: int = 0
    embedding_fallback_count: int = 0
    embedding_error_count: int = 0

    vector_fallback_count: int = 0
    keyword_only_count: int = 0

    def increment_llm_real_call(self) -> None:
        """增加 LLM 真实调用计数"""
        self.llm_real_call_count += 1
        logger.debug("LLM real call count: %d", self.llm_real_call_count)

    def increment_llm_fallback(self) -> None:
        """增加 LLM fallback 计数"""
        self.llm_fallback_count += 1
        logger.warning("LLM fallback count: %d", self.llm_fallback_count)

    def increment_llm_error(self) -> None:
        """增加 LLM 错误计数"""
        self.llm_error_count += 1
        logger.error("LLM error count: %d", self.llm_error_count)

    def increment_embedding_real_call(self) -> None:
        """增加 Embedding 真实调用计数"""
        self.embedding_real_call_count += 1
        logger.debug("Embedding real call count: %d", self.embedding_real_call_count)

    def increment_embedding_fallback(self) -> None:
        """增加 Embedding fallback 计数"""
        self.embedding_fallback_count += 1
        logger.warning("Embedding fallback count: %d", self.embedding_fallback_count)

    def increment_embedding_error(self) -> None:
        """增加 Embedding 错误计数"""
        self.embedding_error_count += 1
        logger.error("Embedding error count: %d", self.embedding_error_count)

    def increment_vector_fallback(self) -> None:
        """增加 Vector fallback 计数"""
        self.vector_fallback_count += 1
        logger.warning("Vector fallback count: %d", self.vector_fallback_count)

    def increment_keyword_only(self) -> None:
        """增加 Keyword-only 计数"""
        self.keyword_only_count += 1
        logger.info(f"📋 Keyword-only recommendation count: {self.keyword_only_count}")

    def get_summary(self) -> Dict[str, int]:
        """
        获取计数器摘要

        Returns:
            Dict[str, int]: 所有计数器的当前值
        """
        return {
            "llm_real_call_count": self.llm_real_call_count,
            "llm_fallback_count": self.llm_fallback_count,
            "llm_error_count": self.llm_error_count,
            "embedding_real_call_count": self.embedding_real_call_count,
            "embedding_fallback_count": self.embedding_fallback_count,
            "embedding_error_count": self.embedding_error_count,
            "vector_fallback_count": self.vector_fallback_count,
            "keyword_only_count": self.keyword_only_count,
        }

    def reset(self) -> None:
        """重置所有计数器（用于测试）"""
        self.llm_real_call_count = 0
        self.llm_fallback_count = 0
        self.llm_error_count = 0
        self.embedding_real_call_count = 0
        self.embedding_fallback_count = 0
        self.embedding_error_count = 0
        self.vector_fallback_count = 0
        self.keyword_only_count = 0
        logger.info("🔧 Service counters reset")

    def print_summary(self) -> None:
        """打印计数器摘要"""
        summary = self.get_summary()
        logger.info("=" * 70)
        logger.info("Service Call Counters Summary")
        logger.info("=" * 70)

        logger.info(f"LLM Real Calls: {summary['llm_real_call_count']}")
        logger.info(f"LLM Fallback: {summary['llm_fallback_count']}")
        logger.info(f"LLM Errors: {summary['llm_error_count']}")

        logger.info(f"Embedding Real Calls: {summary['embedding_real_call_count']}")
        logger.info(f"Embedding Fallback: {summary['embedding_fallback_count']}")
        logger.info(f"Embedding Errors: {summary['embedding_error_count']}")

        logger.info(f"Vector Fallback: {summary['vector_fallback_count']}")
        logger.info(f"Keyword-only Recommendations: {summary['keyword_only_count']}")


# 全局单例
_service_counters: ServiceCounters = ServiceCounters()


def get_service_counters() -> ServiceCounters:
    """获取全局 ServiceCounters 实例"""
    return _service_counters


def reset_service_counters() -> None:
    """重置全局计数器（用于测试）"""
    _service_counters.reset()


__all__ = [
    "ServiceCounters",
    "get_service_counters",
    "reset_service_counters",
]