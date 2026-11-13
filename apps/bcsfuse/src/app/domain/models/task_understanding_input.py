"""
Task Understanding Input Model

M3: Task Understanding Engine

任务理解输入模型，定义理解器的输入结构。

输入侧支持：
- 用户自然语言任务描述（必需）
- 补充上下文（可选）
- 已知约束（可选）
- 已有 Worker / profiling 结果摘要（可选，仅作为上下文）
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TaskUnderstandingInput(BaseModel):
    """
    任务理解输入模型

    封装进入 Task Understanding Engine 的所有输入。
    """

    raw_request: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="用户原始任务描述",
    )
    context: Optional[str] = Field(
        None,
        max_length=50000,
        description="补充上下文信息",
    )
    known_constraints: list[str] = Field(
        default_factory=list,
        description="已知约束条件",
    )
    worker_hints: list[str] = Field(
        default_factory=list,
        description="推荐的 Worker ID 列表（仅作为提示）",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="元数据（如优先级、部门等）",
    )

    model_config = {
        "extra": "forbid",
    }

    @field_validator("raw_request")
    @classmethod
    def validate_raw_request(cls, v: str) -> str:
        """验证原始请求不为空"""
        if not v or not v.strip():
            raise ValueError("raw_request cannot be empty or whitespace only")
        return v

    def has_context(self) -> bool:
        """检查是否有补充上下文"""
        return self.context is not None and self.context.strip() != ""

    def has_constraints(self) -> bool:
        """检查是否有已知约束"""
        return len(self.known_constraints) > 0

    def has_worker_hints(self) -> bool:
        """检查是否有推荐 Worker"""
        return len(self.worker_hints) > 0


__all__ = ["TaskUnderstandingInput"]