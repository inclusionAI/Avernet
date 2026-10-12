"""
OpenClawAdapterResult Domain Model

M9: OpenClaw Adapter

适配器输出结果模型，包含 HandoffBundle、警告、错误和解释。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field

from src.domain.models.handoff_bundle import HandoffBundle


class AdapterExplanation(BaseModel):
    """
    适配器解释

    说明适配过程中的决策。
    """
    subject: str = Field(..., description="解释主题")
    description: str = Field(..., description="解释描述")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    model_config = {
        "extra": "forbid",
    }


class AdapterWarning(BaseModel):
    """
    适配器警告

    表示非致命问题。
    """
    code: str = Field(..., description="警告代码")
    message: str = Field(..., description="警告消息")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    model_config = {
        "extra": "forbid",
    }


class AdapterError(BaseModel):
    """
    适配器错误

    表示致命问题。
    """
    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误消息")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    model_config = {
        "extra": "forbid",
    }


class OpenClawAdapterResult(BaseModel):
    """
    适配器结果

    包含适配出的 HandoffBundle、警告、错误和解释。
    """
    bundle: Optional[HandoffBundle] = Field(
        default=None,
        description="适配出的 handoff bundle"
    )
    warnings: list[AdapterWarning] = Field(
        default_factory=list,
        description="警告列表"
    )
    errors: list[AdapterError] = Field(
        default_factory=list,
        description="错误列表"
    )
    explanations: list[AdapterExplanation] = Field(
        default_factory=list,
        description="解释列表"
    )

    model_config = {
        "extra": "forbid",
    }

    @computed_field
    @property
    def is_success(self) -> bool:
        """判断适配是否成功"""
        return self.bundle is not None and len(self.errors) == 0


__all__ = [
    "OpenClawAdapterResult",
    "AdapterExplanation",
    "AdapterWarning",
    "AdapterError",
]
