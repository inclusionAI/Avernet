"""
PerspectiveProvider Interface

G1: Fusion Entry Layer

视角收集器的抽象接口定义。

职责：
- 定义如何从单个 participant 收集视角
- 支持超时和错误处理
- 供 GroupFusionService 调用
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.domain.models.fusion_result import Perspective


@dataclass(frozen=True)
class PerspectiveContext:
    """
    视角收集上下文

    传递给 PerspectiveProvider 的上下文信息。
    """

    group_id: str
    question: str
    participant_id: str
    driver_bot_id: str | None = None
    timeout_ms: int = 15000


@runtime_checkable
class PerspectiveProvider(Protocol):
    """
    Perspective Provider 接口

    定义如何从单个 participant 收集视角。

    使用 Protocol 允许 duck typing。
    实现者可以是真实的 bot 调用、fake stub 或 mock。
    """

    def collect(self, context: PerspectiveContext) -> Perspective:
        """
        收集单个 participant 的视角

        Args:
            context: 视角收集上下文

        Returns:
            Perspective: 收集到的视角

        Note:
            - 如果收集失败，应返回 status 为 "failed" 或 "timed_out" 的 Perspective
            - 不应抛出异常，错误应在 Perspective 中表达
        """
        ...


__all__ = [
    "PerspectiveProvider",
    "PerspectiveContext",
]