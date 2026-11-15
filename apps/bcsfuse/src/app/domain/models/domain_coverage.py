"""
DomainCoverage

Stage 4: G5 real-context deepening / candidate recommendation 正式接入

领域覆盖分析模型，用于 G5 候选人推荐场景。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DomainCoverage(BaseModel):
    """
    领域覆盖分析模型

    用于分析 G5 候选人推荐场景中领域覆盖情况。

    Attributes:
        required_domains: 问题推断的所需领域列表
        covered_domains: 已覆盖领域列表
        missing_domains: 缺失领域列表
        coverage_score: 覆盖分数 (0-1)
        domain_distribution: 各领域的候选人数量（可选）
    """

    # 必要字段
    required_domains: list[str] = Field(
        default_factory=list,
        description="问题推断的所需领域列表",
    )

    covered_domains: list[str] = Field(
        default_factory=list,
        description="已覆盖领域列表",
    )

    missing_domains: list[str] = Field(
        default_factory=list,
        description="缺失领域列表",
    )

    coverage_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="覆盖分数 (0-1)",
    )

    # 可选字段
    domain_distribution: Optional[dict[str, int]] = Field(
        default=None,
        description="各领域的候选人数量（可选）",
    )

    @property
    def is_fully_covered(self) -> bool:
        """
        是否完全覆盖

        Returns:
            bool: 是否所有所需领域都已覆盖
        """
        return len(self.missing_domains) == 0

    @property
    def coverage_ratio(self) -> float:
        """
        覆盖比例

        Returns:
            float: 已覆盖领域数 / 所需领域数
        """
        if not self.required_domains:
            return 1.0  # 无需领域，视为完全覆盖
        return len(self.covered_domains) / len(self.required_domains)

    def calculate_missing_domains(self) -> list[str]:
        """
        计算缺失领域

        Returns:
            list[str]: 缺失领域列表
        """
        return [d for d in self.required_domains if d not in self.covered_domains]

    model_config = {
        "extra": "forbid",
    }


__all__ = ["DomainCoverage"]