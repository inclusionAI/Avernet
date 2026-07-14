"""
Task Understanding Result Model

M3: Task Understanding Engine

任务理解结果模型，封装理解器的输出结构。

输出侧包含：
- TaskSpec（核心输出）
- warnings（警告信息）
- errors（错误信息）
- source_prompt（原始输入引用）

支持部分成功：有 warnings 但无 errors 仍算成功。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.task_spec import TaskSpec


class UnderstandingWarning(BaseModel):
    """
    理解警告

    表示理解过程中发现的非阻塞性问题。
    """

    field: str = Field(..., description="相关字段")
    message: str = Field(..., description="警告消息")
    suggestion: Optional[str] = Field(None, description="改进建议")

    model_config = {
        "extra": "forbid",
    }


class UnderstandingError(BaseModel):
    """
    理解错误

    表示理解过程中发现的阻塞性问题。
    """

    field: str = Field(..., description="相关字段")
    message: str = Field(..., description="错误消息")
    severity: str = Field(default="medium", description="严重程度")

    model_config = {
        "extra": "forbid",
    }


class TaskUnderstandingResult(BaseModel):
    """
    任务理解结果

    封装 Task Understanding Engine 的输出。
    """

    task_spec: Optional[TaskSpec] = Field(None, description="生成的 TaskSpec")
    warnings: list[UnderstandingWarning] = Field(
        default_factory=list,
        description="警告列表",
    )
    errors: list[UnderstandingError] = Field(
        default_factory=list,
        description="错误列表",
    )
    source_prompt: Optional[str] = Field(None, description="原始用户输入")

    model_config = {
        "extra": "forbid",
    }

    def is_successful(self) -> bool:
        """
        判断理解是否成功

        成功条件：
        - 有有效的 TaskSpec
        - 没有 errors

        Returns:
            bool: 是否成功
        """
        return self.task_spec is not None and len(self.errors) == 0

    def get_summary(self) -> dict:
        """
        获取结果摘要

        Returns:
            dict: 结果摘要
        """
        return {
            "task_id": self.task_spec.id if self.task_spec else None,
            "goal": self.task_spec.goal if self.task_spec else None,
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
            "is_successful": self.is_successful(),
            "unknowns_count": len(self.task_spec.unknowns) if self.task_spec else 0,
        }


__all__ = [
    "TaskUnderstandingResult",
    "UnderstandingWarning",
    "UnderstandingError",
]