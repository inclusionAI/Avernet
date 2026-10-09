"""
CompositionResult Domain Model

M6: Team Composer / Matchmaker

团队组合结果模型，包含 TeamSpec、解释、警告和错误。
"""

from __future__ import annotations

from typing import Optional, Any

from pydantic import BaseModel, Field, field_validator, computed_field

from src.domain.models.team_spec import TeamSpec


class CompositionExplanation(BaseModel):
    """
    组合解释

    说明为什么某个 worker 被选中或排除。
    """
    worker_id: str = Field(..., description="Worker ID")
    role: str = Field(..., description="分配的角色")
    match_score: float = Field(..., ge=0.0, le=1.0, description="匹配分数 (0-1)")
    selection_reason: str = Field(..., description="选择原因")
    capability_match: dict[str, str] = Field(
        default_factory=dict,
        description="能力匹配 {capability_name: level}"
    )
    exclusion_reason: Optional[str] = Field(
        default=None,
        description="排除原因（如果被排除）"
    )

    model_config = {
        "extra": "forbid",
    }


class CompositionWarning(BaseModel):
    """
    组合警告

    表示非致命问题，如部分覆盖、低置信度等。
    """
    code: str = Field(..., description="警告代码")
    message: str = Field(..., description="警告消息")
    details: dict[str, Any] = Field(default_factory=dict, description="详细信息")

    model_config = {
        "extra": "forbid",
    }


class CompositionError(BaseModel):
    """
    组合错误

    表示致命问题，如无法组成团队。
    """
    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误消息")
    details: dict[str, Any] = Field(default_factory=dict, description="详细信息")

    model_config = {
        "extra": "forbid",
    }


class CompositionResult(BaseModel):
    """
    团队组合结果

    包含组合出的 TeamSpec、解释、警告和错误。
    """
    team_spec: Optional[TeamSpec] = Field(
        default=None,
        description="组合出的团队规格"
    )
    explanations: list[CompositionExplanation] = Field(
        default_factory=list,
        description="选择/排除解释"
    )
    warnings: list[CompositionWarning] = Field(
        default_factory=list,
        description="警告列表"
    )
    errors: list[CompositionError] = Field(
        default_factory=list,
        description="错误列表"
    )

    model_config = {
        "extra": "forbid",
    }

    @computed_field
    @property
    def is_success(self) -> bool:
        """判断组合是否成功"""
        # 成功条件：有 team_spec 且没有错误
        return self.team_spec is not None and len(self.errors) == 0


__all__ = [
    "CompositionResult",
    "CompositionExplanation",
    "CompositionWarning",
    "CompositionError",
]