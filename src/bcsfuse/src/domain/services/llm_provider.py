"""
LLMProvider

LLM Gateway / Provider Layer

LLM Provider 协议接口，定义所有 LLM provider 必须实现的接口。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_response import LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    """
    LLM Provider 协议

    定义所有 LLM provider 必须实现的接口。
    这是一个同步接口，支持最小实现。

    Methods:
        generate: 执行 LLM 生成请求
    """

    def generate(
        self,
        request: LLMRequest,
        model: str = None,
    ) -> LLMResponse:
        """
        执行 LLM 生成请求

        Args:
            request: LLM 请求对象
            model: 物理模型名称（可选）

        Returns:
            LLMResponse: LLM 响应对象
        """
        ...


__all__ = [
    "LLMProvider",
]