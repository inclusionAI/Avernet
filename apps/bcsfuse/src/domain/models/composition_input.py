"""
CompositionInput Domain Model

M6: Team Composer / Matchmaker

团队组合输入模型，包含 TaskSpec、PlanDraft、CandidateBundle 和约束条件。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from src.domain.models.task_spec import TaskSpec
from src.domain.models.plan_draft import PlanDraft
from src.domain.models.candidate_bundle import CandidateBundle


class CompositionConstraints(BaseModel):
    """
    组合约束条件

    定义团队组合的限制和偏好。
    """
    max_team_size: Optional[int] = Field(
        default=None,
        ge=1,
        description="最大团队大小，None 表示不限制"
    )
    min_team_size: int = Field(
        default=1,
        ge=1,
        description="最小团队大小"
    )
    require_all_roles: bool = Field(
        default=True,
        description="是否要求所有角色都被分配"
    )
    balance_workload: bool = Field(
        default=True,
        description="是否平衡工作负载"
    )
    prefer_diverse_capabilities: bool = Field(
        default=False,
        description="是否优先选择多样化能力"
    )

    model_config = {
        "extra": "forbid",
    }

    @model_validator(mode="after")
    def validate_team_size(self) -> "CompositionConstraints":
        """验证团队大小约束"""
        if self.max_team_size is not None and self.min_team_size > self.max_team_size:
            raise ValueError(
                f"min_team_size ({self.min_team_size}) cannot exceed "
                f"max_team_size ({self.max_team_size})"
            )
        return self


class CompositionInput(BaseModel):
    """
    团队组合输入

    包含进行团队组合所需的所有输入数据：
    - TaskSpec: 任务规格
    - PlanDraft: 规划草案
    - CandidateBundle: 候选集
    - CompositionConstraints: 组合约束
    """
    task_spec: TaskSpec = Field(..., description="任务规格")
    plan_draft: PlanDraft = Field(..., description="规划草案")
    candidate_bundle: CandidateBundle = Field(..., description="候选集")
    constraints: CompositionConstraints = Field(
        default_factory=CompositionConstraints,
        description="组合约束"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "CompositionInput",
    "CompositionConstraints",
]