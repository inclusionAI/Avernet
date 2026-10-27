"""
Reranker 抽象层 - 简化版
"""

from dataclasses import dataclass


@dataclass
class RerankResult:
    """Rerank 结果"""
    candidate_id: str
    score: float
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class Reranker:
    """Reranker 抽象基类"""

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[RerankResult]:
        """
        执行重排序

        Args:
            query: 查询文本
            candidates: 候选列表，每个候选是 {"id": str, "text": str} 的字典
            top_k: 返回数量

        Returns:
            按分数降序排列的结果
        """
        raise NotImplementedError
