"""
LLMResponse

LLM Gateway / Provider Layer

LLM 响应模型，描述 LLM 返回的完整响应。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class FinishReason(str, Enum):
    """
    完成原因枚举

    描述 LLM 生成结束的原因。
    """

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class LLMUsage(BaseModel):
    """
    LLM 使用量

    记录 token 使用情况。

    Attributes:
        input_tokens: 输入 token 数
        output_tokens: 输出 token 数
        total_tokens: 总 token 数
    """

    model_config = {"extra": "forbid"}

    input_tokens: int = Field(
        default=0,
        ge=0,
        description="输入 token 数",
    )

    output_tokens: int = Field(
        default=0,
        ge=0,
        description="输出 token 数",
    )

    total_tokens: int = Field(
        default=0,
        ge=0,
        description="总 token 数",
    )


class LLMError(BaseModel):
    """
    LLM 错误

    记录 LLM 调用过程中的错误信息。

    Attributes:
        code: 错误代码
        message: 错误消息
        details: 错误详情列表
    """

    model_config = {"extra": "forbid"}

    code: str = Field(
        max_length=64,
        description="错误代码",
    )

    message: str = Field(
        max_length=2000,
        description="错误消息",
    )

    details: list[str] = Field(
        default_factory=list,
        description="错误详情列表",
    )


class LLMResponse(BaseModel):
    """
    LLM 响应

    描述 LLM 返回的完整响应，包括原始文本、结构化数据和元数据。

    Attributes:
        provider_id: Provider 标识符
        model_id: 模型标识符
        raw_text: 原始响应文本
        structured_data: 解析后的结构化数据（JSON）
        parse_success: 结构化解析是否成功
        latency_ms: 响应延迟（毫秒）
        usage: token 使用量
        finish_reason: 完成原因
        warnings: 警告信息列表
        errors: 错误信息列表
    """

    model_config = {"extra": "forbid"}

    provider_id: str = Field(
        max_length=64,
        description="Provider 标识符",
    )

    model_id: str = Field(
        max_length=128,
        description="模型标识符",
    )

    raw_text: str = Field(
        default="",
        description="原始响应文本",
    )

    structured_data: Optional[dict[str, Any]] = Field(
        default=None,
        description="解析后的结构化数据",
    )

    parse_success: bool = Field(
        description="结构化解析是否成功",
    )

    latency_ms: int = Field(
        ge=0,
        description="响应延迟（毫秒）",
    )

    usage: Optional[LLMUsage] = Field(
        default=None,
        description="token 使用量",
    )

    finish_reason: FinishReason = Field(
        description="完成原因",
    )

    warnings: list[str] = Field(
        default_factory=list,
        description="警告信息列表",
    )

    errors: list[LLMError] = Field(
        default_factory=list,
        description="错误信息列表",
    )


__all__ = [
    "LLMResponse",
    "LLMUsage",
    "FinishReason",
    "LLMError",
]