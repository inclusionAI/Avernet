"""
Matchmaker Protocol

M6: Team Composer / Matchmaker

定义团队组合匹配器接口，用于从候选集中选择最佳团队配置。

Matchmaker 的职责：
- 接收 CompositionInput
- 根据任务需求和候选集进行匹配和选择
- 返回 CompositionResult

Matchmaker 不负责：
- 检索候选集（由 Retriever 负责）
- 决策组合策略（由上层服务决定）
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.composition_input import CompositionInput
from src.domain.models.composition_result import CompositionResult


@runtime_checkable
class Matchmaker(Protocol):
    """
    团队组合匹配器协议

    定义从候选集中选择和匹配 Worker 组成团队的接口。

    方法：
        compose: 执行团队组合，返回 CompositionResult
    """

    def compose(self, input_data: CompositionInput) -> CompositionResult:
        """
        执行团队组合

        Args:
            input_data: 组合输入，包含 TaskSpec、PlanDraft、CandidateBundle 和约束条件

        Returns:
            CompositionResult: 组合结果，包含 TeamSpec、解释、警告和错误
        """
        ...


__all__ = ["Matchmaker"]