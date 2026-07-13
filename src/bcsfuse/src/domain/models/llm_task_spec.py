"""
LLMTaskSpec

LLM Gateway / Provider Layer

LLM 任务规格模型，用于描述 LLM 任务的特征和约束。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """
    任务类型枚举

    定义系统支持的 LLM 任务类型。
    """

    FUSION_RECOMMENDATION = "fusion_recommendation"
    TASK_UNDERSTANDING = "task_understanding"
    PLANNING = "planning"
    EXTRACTION = "extraction"
    SUMMARY = "summary"
    RATIONALE_GENERATION = "rationale_generation"
    PROFILE_ANALYSIS = "profile_analysis"
    PROFILE_FUSION = "bot_profile_fuse"  # G9: Bot Profile Fuse


class Complexity(str, Enum):
    """
    复杂度枚举

    描述任务的处理复杂程度。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CostSensitivity(str, Enum):
    """
    成本敏感度枚举

    描述任务对成本的关注程度。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LLMTaskSpec(BaseModel):
    """
    LLM 任务规格

    描述一个 LLM 任务的特征、约束和偏好，用于路由和治理决策。

    Attributes:
        task_type: 任务类型，决定路由策略
        complexity: 任务复杂度，影响模型选择
        need_structured_output: 是否需要结构化输出（JSON）
        context_size: 上下文大小估算（token 数）
        cost_sensitivity: 成本敏感度
        require_explanation: 是否需要可解释性输出
        latency_budget_ms: 延迟预算（毫秒）
    """

    model_config = {"extra": "forbid"}

    task_type: TaskType = Field(
        description="任务类型",
    )

    complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="任务复杂度",
    )

    need_structured_output: bool = Field(
        default=False,
        description="是否需要结构化输出（JSON）",
    )

    context_size: int = Field(
        default=0,
        ge=0,
        description="上下文大小估算（token 数）",
    )

    cost_sensitivity: CostSensitivity = Field(
        default=CostSensitivity.MEDIUM,
        description="成本敏感度",
    )

    require_explanation: bool = Field(
        default=False,
        description="是否需要可解释性输出",
    )

    latency_budget_ms: int = Field(
        default=15000,
        ge=1000,
        le=120000,
        description="延迟预算（毫秒）",
    )


__all__ = [
    "LLMTaskSpec",
    "TaskType",
    "Complexity",
    "CostSensitivity",
]