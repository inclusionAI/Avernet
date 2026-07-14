"""
LLMRouter

LLM Gateway / Provider Layer

LLM Router 协议接口，定义任务路由到模型的策略接口。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.llm_task_spec import LLMTaskSpec
from src.domain.models.model_profile import ModelProfile


@runtime_checkable
class LLMRouter(Protocol):
    """
    LLM Router 协议

    定义任务规格到模型档案的路由策略接口。

    Methods:
        route: 根据任务规格路由到合适的模型
    """

    def route(self, task_spec: LLMTaskSpec) -> ModelProfile:
        """
        根据任务规格路由到合适的模型

        Args:
            task_spec: LLM 任务规格

        Returns:
            ModelProfile: 选中的模型档案
        """
        ...


__all__ = [
    "LLMRouter",
]