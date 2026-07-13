"""
LLMRequest

LLM Gateway / Provider Layer

LLM 请求模型，描述发送给 LLM 的完整请求。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.llm_task_spec import LLMTaskSpec


class LLMRequestMetadata(BaseModel):
    """
    LLM 请求元数据

    用于追踪和关联请求的额外信息。

    Attributes:
        trace_id: 追踪 ID
        session_id: 会话 ID
        source: 请求来源（服务名）
    """

    model_config = {"extra": "forbid"}

    trace_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="追踪 ID",
    )

    session_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="会话 ID",
    )

    source: Optional[str] = Field(
        default=None,
        max_length=64,
        description="请求来源（服务名）",
    )


class LLMRequest(BaseModel):
    """
    LLM 请求

    描述发送给 LLM 的完整请求，包含任务规格、提示词和配置。

    Attributes:
        task_spec: 任务规格，用于路由决策
        system_prompt: 系统提示词（可选）
        user_prompt: 用户提示词（必须）
        expected_schema_name: 期望的输出 schema 名称（用于结构化输出）
        temperature: 温度参数
        max_tokens: 最大生成 token 数
        metadata: 请求元数据
    """

    model_config = {"extra": "forbid"}

    task_spec: LLMTaskSpec = Field(
        description="任务规格",
    )

    system_prompt: Optional[str] = Field(
        default=None,
        max_length=32000,
        description="系统提示词",
    )

    user_prompt: str = Field(
        min_length=1,
        max_length=128000,
        description="用户提示词",
    )

    expected_schema_name: Optional[str] = Field(
        default=None,
        max_length=128,
        description="期望的输出 schema 名称",
    )

    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="温度参数",
    )

    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=128000,
        description="最大生成 token 数",
    )

    metadata: Optional[LLMRequestMetadata] = Field(
        default=None,
        description="请求元数据",
    )


__all__ = [
    "LLMRequest",
    "LLMRequestMetadata",
]