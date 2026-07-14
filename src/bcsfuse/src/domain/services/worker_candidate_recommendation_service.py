"""
WorkerCandidateRecommendationService

Stage 4: G5 real-context deepening / candidate recommendation 正式接入

G5 候选人推荐服务接口，用于推荐专家候选人。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.domain.models.candidate_recommendation import CandidateRecommendationResponse

if TYPE_CHECKING:
    from src.domain.models.retrieval_mode import RetrievalMode


@runtime_checkable
class WorkerCandidateRecommendationService(Protocol):
    """
    Worker Candidate Recommendation 服务接口

    用于 G5 Expert Diagnosis 模式的候选人推荐。

    职责：
    1. 当 participants 缺失时推荐候选人
    2. 当 participants 不足时补充推荐
    3. 显式 participants 优先，补充推荐标记 is_supplement=True
    4. 返回领域覆盖分析

    使用 Protocol 允许 duck typing。
    实现者可以是真实实现、fake stub 或 mock。
    """

    def recommend(
        self,
        question: str,
        mode: "RetrievalMode",
        participants: list[str] | None = None,
        max_candidates: int = 5,
        min_experts: int = 3,
        strict_participants: bool = False,
        runtime_config: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.01,
    ) -> CandidateRecommendationResponse:
        """
        推荐候选人

        Args:
            question: 问题/任务描述
            mode: 检索模式
            participants: 显式 participants 列表（可选）
            max_candidates: 最大候选人数
            min_experts: 最小专家数阈值
            strict_participants: 是否启用严格参与者模式
                - False（默认）: 允许补充推荐
                - True: 禁止补充推荐，只返回显式找到的 participants
            runtime_config: 向量搜索运行时配置（可选）
                - expand_factor: int (1-10)
                - reranker_model: str | None
            filters: 元数据过滤条件（可选）
                - availability: list[str] - 可用性过滤，如 ["protected", "public"]
                - runtime_state: list[str] - 运行时状态过滤，如 ["online"]
            min_score: 最小相似度阈值（默认 0.01）

        Returns:
            CandidateRecommendationResponse: 推荐响应

        规则：
        1. 显式 participants 优先保留（is_supplement=False）
        2. 如果 participants 不足且 strict_participants=False，补充推荐（is_supplement=True）
        3. 如果 strict_participants=True，禁止补充推荐
        4. 结果顺序：显式 participants + 补充推荐（如果允许）
        5. 补充推荐要考虑领域覆盖
        """
        ...


__all__ = ["WorkerCandidateRecommendationService"]