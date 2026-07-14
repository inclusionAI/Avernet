"""
Retriever Interface

M5: Unified Retrieval Fabric

Retriever 接口定义，供 infra 层实现。

职责：
- 定义检索的抽象接口
- 不依赖具体实现
- 供 application 层调用
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.retrieval_input import RetrievalInput
from src.domain.models.retrieval_result import RetrievalResult


@runtime_checkable
class Retriever(Protocol):
    """
    Retriever 接口

    定义检索的基本操作。

    使用 Protocol 而非 ABC，允许 duck typing，
    但仍能通过 isinstance 检查。
    """

    def retrieve(self, input_data: RetrievalInput) -> RetrievalResult:
        """
        执行检索

        Args:
            input_data: 检索输入，包含 TaskSpec、PlanDraft 和可选的过滤条件

        Returns:
            RetrievalResult: 检索结果，包含候选集、警告、错误和解释

        Raises:
            RetrievalException: 检索过程中的严重错误
        """
        ...


__all__ = ["Retriever"]