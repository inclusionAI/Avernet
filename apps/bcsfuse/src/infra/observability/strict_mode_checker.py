"""
Strict Mode Checker - 严格模式校验器

确保系统真实调用了 LLM/Embedding，而非使用 stub/fallback/fake。

使用场景：
- REQUIRE_REAL_LLM=true：如果 G1/G2/G5 走了 stub/fallback/fake，测试直接失败
- REQUIRE_REAL_EMBEDDING=true：如果 recommendation 没走 real embedding，测试直接失败

使用方式：
    from src.infra.observability.strict_mode_checker import StrictModeChecker

    checker = StrictModeChecker()

    # LLM 调用前
    checker.check_llm_before_call("G1", model="fast-model")

    # LLM 调用后
    checker.check_llm_after_call(
        "G1",
        model="fast-model",
        was_real_call=True,
        provider="anthropic_compatible",
        latency_ms=1234,
    )

    # 测试结束时验证
    checker.validate()
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class StrictModeViolation(Exception):
    """Strict Mode 违规异常"""

    def __init__(self, message: str, violations: list[str]):
        self.message = message
        self.violations = violations
        super().__init__(f"{message}\nViolations:\n" + "\n".join(f"  - {v}" for v in violations))


@dataclass
class LLCCallRecord:
    """LLM 调用记录"""
    component: str  # G1/G2/G5
    model: str
    was_real_call: bool
    provider: str
    latency_ms: int
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class EmbeddingCallRecord:
    """Embedding 调用记录"""
    component: str  # recommendation/vector_match
    was_real_call: bool
    provider: str
    latency_ms: int
    timestamp: datetime = field(default_factory=datetime.now)


class StrictModeChecker:
    """
    Strict Mode Checker

    确保系统真实调用了 LLM/Embedding 服务。

    功能：
    1. 记录所有 LLM/Embedding 调用
    2. 验证调用是否真实（而非 stub/fallback/fake）
    3. 测试结束时生成报告

    环境变量：
    - REQUIRE_REAL_LLM: 如果为 true，LLM 必须真实调用
    - REQUIRE_REAL_EMBEDDING: 如果为 true，Embedding 必须真实调用
    """

    def __init__(self):
        """初始化"""
        self._require_real_llm = os.environ.get("REQUIRE_REAL_LLM", "false").lower() == "true"
        self._require_real_embedding = os.environ.get("REQUIRE_REAL_EMBEDDING", "false").lower() == "true"

        self._llm_calls: list[LLCCallRecord] = []
        self._embedding_calls: list[EmbeddingCallRecord] = []

        self._violations: list[str] = []

        logger.debug(
            "Strict mode checker initialized: "
            "REQUIRE_REAL_LLM=%s, REQUIRE_REAL_EMBEDDING=%s",
            self._require_real_llm, self._require_real_embedding
        )

    def check_llm_before_call(self, component: str, model: str) -> None:
        """
        LLM 调用前检查

        Args:
            component: 组件名称（G1/G2/G5）
            model: 模型名称
        """
        logger.debug(f"LLM call starting: component={component}, model={model}")

    def check_llm_after_call(
        self,
        component: str,
        model: str,
        was_real_call: bool,
        provider: str,
        latency_ms: int,
        fallback_reason: Optional[str] = None,
    ) -> None:
        """
        LLM 调用后检查

        Args:
            component: 组件名称（G1/G2/G5）
            model: 模型名称
            was_real_call: 是否真实调用
            provider: Provider 名称
            latency_ms: 延迟（毫秒）
            fallback_reason: 如果是 fallback，原因是什么
        """
        # 记录调用
        record = LLCCallRecord(
            component=component,
            model=model,
            was_real_call=was_real_call,
            provider=provider,
            latency_ms=latency_ms,
        )
        self._llm_calls.append(record)

        # 判断是否为 fake provider
        is_fake = provider.lower() in ["fake", "stub", "mock"]

        # 如果需要真实调用但发生了 fallback/fake
        if self._require_real_llm and (not was_real_call or is_fake):
            violation = (
                f"[{component}] LLM call was not real: "
                f"provider={provider}, model={model}, "
                f"was_real_call={was_real_call}, "
                f"fallback_reason={fallback_reason or 'N/A'}"
            )
            self._violations.append(violation)

            # 记录日志
            logger.error("STRICT MODE VIOLATION: %s", violation)
        else:
            logger.debug(
                "LLM call recorded: component=%s, provider=%s, model=%s, real=%s, latency=%dms",
                component, provider, model, was_real_call, latency_ms
            )

    def check_embedding_after_call(
        self,
        component: str,
        was_real_call: bool,
        provider: str,
        latency_ms: int,
        fallback_reason: Optional[str] = None,
    ) -> None:
        """
        Embedding 调用后检查

        Args:
            component: 组件名称
            was_real_call: 是否真实调用
            provider: Provider 名称
            latency_ms: 延迟（毫秒）
            fallback_reason: 如果是 fallback，原因是什么
        """
        # 记录调用
        record = EmbeddingCallRecord(
            component=component,
            was_real_call=was_real_call,
            provider=provider,
            latency_ms=latency_ms,
        )
        self._embedding_calls.append(record)

        # 判断是否为 fake provider
        is_fake = provider.lower() in ["fake", "stub", "mock"]

        # 如果需要真实调用但发生了 fallback/fake
        if self._require_real_embedding and (not was_real_call or is_fake):
            violation = (
                f"[{component}] Embedding call was not real: "
                f"provider={provider}, "
                f"was_real_call={was_real_call}, "
                f"fallback_reason={fallback_reason or 'N/A'}"
            )
            self._violations.append(violation)

            # 记录日志
            logger.error("STRICT MODE VIOLATION: %s", violation)
        else:
            logger.debug(
                "Embedding call recorded: component=%s, provider=%s, real=%s, latency=%dms",
                component, provider, was_real_call, latency_ms
            )

    def validate(self) -> None:
        """
        验证所有调用是否符合 strict mode 要求

        Raises:
            StrictModeViolation: 如果有违规行为
        """
        logger.info("=" * 70)
        logger.info("Strict Mode Validation")
        logger.info("=" * 70)

        logger.info(f"REQUIRE_REAL_LLM: {self._require_real_llm}")
        logger.info(f"REQUIRE_REAL_EMBEDDING: {self._require_real_embedding}")

        logger.info(f"\nLLM Calls ({len(self._llm_calls)} total):")
        for call in self._llm_calls:
            status = "✅ REAL" if call.was_real_call else "❌ FAKE/FALLBACK"
            logger.info(
                f"  {status} [{call.component}] "
                f"provider={call.provider} "
                f"model={call.model} "
                f"latency={call.latency_ms}ms"
            )

        logger.info(f"\nEmbedding Calls ({len(self._embedding_calls)} total):")
        for call in self._embedding_calls:
            status = "✅ REAL" if call.was_real_call else "❌ FAKE/FALLBACK"
            logger.info(
                f"  {status} [{call.component}] "
                f"provider={call.provider} "
                f"latency={call.latency_ms}ms"
            )

        if self._violations:
            logger.error(f"\n❌ VIOLATIONS ({len(self._violations)}):")
            for v in self._violations:
                logger.error(f"  - {v}")

            raise StrictModeViolation(
                f"Strict mode validation failed with {len(self._violations)} violations",
                self._violations,
            )
        else:
            logger.info("\n✅ All strict mode checks passed!")

    def get_summary(self) -> dict:
        """
        获取调用摘要

        Returns:
            dict: 包含所有调用统计信息
        """
        llm_real_count = sum(1 for call in self._llm_calls if call.was_real_call)
        llm_fake_count = len(self._llm_calls) - llm_real_count

        embedding_real_count = sum(1 for call in self._embedding_calls if call.was_real_call)
        embedding_fake_count = len(self._embedding_calls) - embedding_real_count

        return {
            "require_real_llm": self._require_real_llm,
            "require_real_embedding": self._require_real_embedding,
            "llm_calls": {
                "total": len(self._llm_calls),
                "real_count": llm_real_count,
                "fake_count": llm_fake_count,
            },
            "embedding_calls": {
                "total": len(self._embedding_calls),
                "real_count": embedding_real_count,
                "fake_count": embedding_fake_count,
            },
            "violations": self._violations,
        }

    def reset(self) -> None:
        """重置所有记录（用于测试）"""
        self._llm_calls.clear()
        self._embedding_calls.clear()
        self._violations.clear()
        logger.info("🔧 Strict mode checker reset")


# 全局单例
_strict_mode_checker: Optional[StrictModeChecker] = None


def get_strict_mode_checker() -> StrictModeChecker:
    """获取全局 StrictModeChecker 实例"""
    global _strict_mode_checker
    if _strict_mode_checker is None:
        _strict_mode_checker = StrictModeChecker()
    return _strict_mode_checker


def reset_strict_mode_checker() -> None:
    """重置全局实例（用于测试）"""
    global _strict_mode_checker
    if _strict_mode_checker is not None:
        _strict_mode_checker.reset()


__all__ = [
    "StrictModeChecker",
    "StrictModeViolation",
    "get_strict_mode_checker",
    "reset_strict_mode_checker",
]