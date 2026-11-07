"""
ParticipantsSufficiencyChecker

Stage 4: G5 real-context deepening / candidate recommendation 正式接入

Participants 充足性检查器，用于判断显式 participants 是否充足。

规则（钉死）：
1. participants is None → 不足
2. len(explicit_participants) < min_experts → 不足
3. required_domains 未覆盖 → 不足
4. 其余 → 充足
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SufficiencyCheckResult:
    """
    充足性检查结果

    Attributes:
        is_sufficient: 是否充足
        reason: 不充足的原因（如果不足）
        participant_count: 参与者数量
        min_required: 最小要求数
        uncovered_domains: 未覆盖的领域列表
    """
    is_sufficient: bool
    reason: Optional[str] = None
    participant_count: int = 0
    min_required: int = 3
    uncovered_domains: list[str] = None

    def __post_init__(self):
        if self.uncovered_domains is None:
            self.uncovered_domains = []


class ParticipantsSufficiencyChecker:
    """
    Participants 充足性检查器

    用于判断显式 participants 是否满足 G5 专家诊断的要求。

    规则（按优先级）：
    1. participants is None → 不足 (reason="no_participants_given")
    2. len(explicit_participants) < min_experts → 不足 (reason="insufficient_count")
    3. required_domains 未被 covered_domains 完全覆盖 → 不足 (reason="domains_not_covered")
    4. 其余 → 充足

    Attributes:
        min_experts: 最小专家数阈值（默认 3）
    """

    def __init__(self, min_experts: int = 3):
        """
        初始化检查器

        Args:
            min_experts: 最小专家数阈值
        """
        self.min_experts = min_experts

    def check(
        self,
        participants: Optional[list[str]],
        covered_domains: Optional[list[str]] = None,
        required_domains: Optional[list[str]] = None,
    ) -> SufficiencyCheckResult:
        """
        检查 participants 充足性

        Args:
            participants: 显式 participants 列表
            covered_domains: 已覆盖的领域列表
            required_domains: 问题推断的所需领域列表

        Returns:
            SufficiencyCheckResult: 检查结果
        """
        # 规则 1: participants is None
        if participants is None:
            return SufficiencyCheckResult(
                is_sufficient=False,
                reason="no_participants_given",
                participant_count=0,
                min_required=self.min_experts,
                uncovered_domains=required_domains or [],
            )

        participant_count = len(participants)

        # 规则 2: len(explicit_participants) < min_experts
        if participant_count < self.min_experts:
            return SufficiencyCheckResult(
                is_sufficient=False,
                reason="insufficient_count",
                participant_count=participant_count,
                min_required=self.min_experts,
                uncovered_domains=required_domains or [],
            )

        # 规则 3: required_domains 未被完全覆盖
        if required_domains:
            covered = set(covered_domains or [])
            required = set(required_domains)
            uncovered = list(required - covered)

            if uncovered:
                return SufficiencyCheckResult(
                    is_sufficient=False,
                    reason="domains_not_covered",
                    participant_count=participant_count,
                    min_required=self.min_experts,
                    uncovered_domains=uncovered,
                )

        # 规则 4: 充足
        return SufficiencyCheckResult(
            is_sufficient=True,
            participant_count=participant_count,
            min_required=self.min_experts,
            uncovered_domains=[],
        )

    def is_sufficient(
        self,
        participants: Optional[list[str]],
        covered_domains: Optional[list[str]] = None,
        required_domains: Optional[list[str]] = None,
    ) -> bool:
        """
        简化的充足性检查

        Args:
            participants: 显式 participants 列表
            covered_domains: 已覆盖的领域列表
            required_domains: 问题推断的所需领域列表

        Returns:
            bool: 是否充足
        """
        result = self.check(participants, covered_domains, required_domains)
        return result.is_sufficient


__all__ = [
    "ParticipantsSufficiencyChecker",
    "SufficiencyCheckResult",
]