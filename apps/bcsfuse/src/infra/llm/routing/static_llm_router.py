"""
StaticLLMRouter

LLM Gateway / Provider Layer

静态任务路由器，根据任务类型路由到合适的模型。
"""

from __future__ import annotations

from typing import Optional

from src.domain.services.llm_router import LLMRouter
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
from src.domain.models.model_profile import ModelProfile
from src.infra.llm.config.llm_settings import LLMSettings


# 静态路由规则：任务类型 -> (主模型, 备用模型)
ROUTING_RULES: dict[TaskType, tuple[str, Optional[str]]] = {
    TaskType.FUSION_RECOMMENDATION: ("reasoning.default", "balanced.default"),
    TaskType.TASK_UNDERSTANDING: ("balanced.default", "fast.default"),
    TaskType.PLANNING: ("reasoning.default", "balanced.default"),
    TaskType.EXTRACTION: ("extraction.default", "balanced.default"),
    TaskType.SUMMARY: ("fast.default", "balanced.default"),
    TaskType.RATIONALE_GENERATION: ("reasoning.default", "balanced.default"),
    TaskType.PROFILE_ANALYSIS: ("extraction.default", "balanced.default"),
}


class StaticLLMRouter(LLMRouter):
    """
    静态任务路由器

    根据预定义的静态规则，将任务类型路由到合适的模型。

    Attributes:
        settings: LLM 配置
    """

    def __init__(self, settings: Optional[LLMSettings] = None):
        """
        初始化路由器

        Args:
            settings: LLM 配置（可选，默认创建新实例）
        """
        self._settings = settings or LLMSettings()

    def route(self, task_spec: LLMTaskSpec) -> ModelProfile:
        """
        根据任务规格路由到合适的模型

        Args:
            task_spec: LLM 任务规格

        Returns:
            ModelProfile: 选中的模型档案

        Raises:
            ValueError: 无法找到合适的模型
        """
        task_type = task_spec.task_type

        # 获取路由规则
        if task_type not in ROUTING_RULES:
            # 未知任务类型，使用均衡模型
            primary_model = "balanced.default"
        else:
            primary_model, _ = ROUTING_RULES[task_type]

        # 获取模型档案
        profile = self._settings.get_model_profile(primary_model)

        if profile is None:
            raise ValueError(f"Model profile not found: {primary_model}")

        return profile

    def get_fallback_model(self, task_spec: LLMTaskSpec) -> Optional[ModelProfile]:
        """
        获取任务的备用模型

        Args:
            task_spec: LLM 任务规格

        Returns:
            ModelProfile 或 None
        """
        task_type = task_spec.task_type

        if task_type not in ROUTING_RULES:
            return None

        _, fallback_model = ROUTING_RULES[task_type]

        if fallback_model is None:
            return None

        return self._settings.get_model_profile(fallback_model)


__all__ = [
    "StaticLLMRouter",
    "ROUTING_RULES",
]