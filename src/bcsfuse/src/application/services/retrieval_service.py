"""
Retrieval Service

M5: Unified Retrieval Fabric

检索服务，负责编排检索流程并返回结果。

职责：
- 接收 RetrievalInput
- 调用 Retriever 执行检索
- 汇总并返回 RetrievalResult

不负责：
- 具体检索逻辑
- 过滤规则的实现
"""

from __future__ import annotations

from src.domain.services.retriever import Retriever
from src.domain.models.retrieval_input import RetrievalInput
from src.domain.models.retrieval_result import RetrievalResult


class RetrievalService:
    """
    检索服务

    负责编排检索流程，调用 Retriever 执行检索，
    并返回完整的 RetrievalResult。

    Fields:
        retriever: Retriever 实例
    """

    def __init__(self, retriever: Retriever):
        """
        初始化 RetrievalService

        Args:
            retriever: Retriever 实例，用于执行检索
        """
        self._retriever = retriever

    @property
    def retriever(self) -> Retriever:
        """获取 retriever"""
        return self._retriever

    def retrieve(self, input_data: RetrievalInput) -> RetrievalResult:
        """
        执行检索

        Args:
            input_data: 检索输入，包含 TaskSpec、PlanDraft 和可选的过滤条件

        Returns:
            RetrievalResult: 检索结果，包含候选集、警告、错误和解释
        """
        # 直接调用 retriever 执行检索
        # retriever 负责所有的检索逻辑、过滤、排序和解释生成
        result = self._retriever.retrieve(input_data)

        return result


__all__ = ["RetrievalService"]