"""
CompilerResult Domain Model

M8: Execution Packet Compiler

编译器输出结果模型，包含 ExecutionPacket、警告、错误和解释。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field

from src.domain.models.execution_packet import ExecutionPacket


class CompilerExplanation(BaseModel):
    """
    编译解释

    说明编译过程中的决策。
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


class CompilerWarning(BaseModel):
    """
    编译警告

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


class CompilerError(BaseModel):
    """
    编译错误

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


class CompilerResult(BaseModel):
    """
    编译器结果

    包含编译出的 ExecutionPacket、警告、错误和解释。
    """
    packet: Optional[ExecutionPacket] = Field(
        default=None,
        description="编译出的执行包"
    )
    warnings: list[CompilerWarning] = Field(
        default_factory=list,
        description="警告列表"
    )
    errors: list[CompilerError] = Field(
        default_factory=list,
        description="错误列表"
    )
    explanations: list[CompilerExplanation] = Field(
        default_factory=list,
        description="解释列表"
    )

    model_config = {
        "extra": "forbid",
    }

    @computed_field
    @property
    def is_success(self) -> bool:
        """判断编译是否成功"""
        return self.packet is not None and len(self.errors) == 0


__all__ = [
    "CompilerResult",
    "CompilerExplanation",
    "CompilerWarning",
    "CompilerError",
]