"""
TeamCompositionService

M6: Team Composer / Matchmaker

团队组合服务，负责编排团队组合流程并返回结果。

职责：
- 接收 CompositionInput
- 调用 Matchmaker 执行组合
- 汇总并返回 CompositionResult

不负责：
- 具体匹配逻辑
- 检索候选集
"""

from __future__ import annotations

from src.domain.services.matchmaker import Matchmaker
from src.domain.models.composition_input import CompositionInput
from src.domain.models.composition_result import CompositionResult


class TeamCompositionService:
    """
    团队组合服务

    负责编排团队组合流程，调用 Matchmaker 执行组合，
    并返回完整的 CompositionResult。

    Fields:
        matchmaker: Matchmaker 实例
    """

    def __init__(self, matchmaker: Matchmaker):
        """
        初始化 TeamCompositionService

        Args:
            matchmaker: Matchmaker 实例，用于执行团队组合
        """
        self._matchmaker = matchmaker

    @property
    def matchmaker(self) -> Matchmaker:
        """获取 matchmaker"""
        return self._matchmaker

    def compose(self, input_data: CompositionInput) -> CompositionResult:
        """
        执行团队组合

        Args:
            input_data: 组合输入，包含 TaskSpec、PlanDraft、CandidateBundle 和约束条件

        Returns:
            CompositionResult: 组合结果，包含 TeamSpec、警告、错误和解释
        """
        # 直接调用 matchmaker 执行组合
        # matchmaker 负责所有的匹配、选择和解释生成
        result = self._matchmaker.compose(input_data)

        return result


__all__ = ["TeamCompositionService"]