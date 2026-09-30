"""
ModelProfile

LLM Gateway / Provider Layer

模型档案模型，描述一个 LLM 模型的配置和能力。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.llm_task_spec import TaskType


class ModelTier(str, Enum):
    """
    模型层级枚举

    描述模型的定位层次。
    """

    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"
    LONG_CONTEXT = "long_context"
    EXTRACTION = "extraction"


class CostClass(str, Enum):
    """
    成本类枚举

    描述模型的成本级别。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LatencyClass(str, Enum):
    """
    延迟类枚举

    描述模型的响应延迟级别。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelProfile(BaseModel):
    """
    模型档案

    描述一个 LLM 模型的配置、能力和推荐用途。

    Attributes:
        logical_model_id: 逻辑模型标识符（如 fast.default）
        provider_id: Provider 标识符
        physical_model_name: 物理模型名称（厂商模型名）
        tier: 模型层级
        supports_json: 是否支持 JSON 结构化输出
        supports_long_context: 是否支持长上下文
        cost_class: 成本级别
        latency_class: 延迟级别
        recommended_for: 推荐的任务类型列表
        max_context_tokens: 最大上下文长度
        description: 模型描述
    """

    model_config = {"extra": "forbid"}

    logical_model_id: str = Field(
        min_length=1,
        max_length=64,
        description="逻辑模型标识符（如 fast.default）",
    )

    provider_id: str = Field(
        min_length=1,
        max_length=64,
        description="Provider 标识符",
    )

    physical_model_name: str = Field(
        min_length=1,
        max_length=128,
        description="物理模型名称（厂商模型名）",
    )

    tier: ModelTier = Field(
        description="模型层级",
    )

    supports_json: bool = Field(
        default=False,
        description="是否支持 JSON 结构化输出",
    )

    supports_long_context: bool = Field(
        default=False,
        description="是否支持长上下文",
    )

    cost_class: CostClass = Field(
        default=CostClass.MEDIUM,
        description="成本级别",
    )

    latency_class: LatencyClass = Field(
        default=LatencyClass.MEDIUM,
        description="延迟级别",
    )

    recommended_for: list[TaskType] = Field(
        default_factory=list,
        description="推荐的任务类型列表",
    )

    max_context_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="最大上下文长度",
    )

    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="模型描述",
    )


__all__ = [
    "ModelProfile",
    "ModelTier",
    "CostClass",
    "LatencyClass",
]