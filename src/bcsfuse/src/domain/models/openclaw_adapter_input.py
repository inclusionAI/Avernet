"""
OpenClawAdapterInput Domain Model

M9: OpenClaw Adapter

适配器输入模型，包含 ExecutionPacket 和可选配置。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.models.execution_packet import ExecutionPacket


class AdapterOptions(BaseModel):
    """
    适配器选项

    控制 OpenClaw adapter 行为的可选参数。
    """
    include_memory_files: bool = Field(
        default=True,
        description="是否包含 memory 相关文件"
    )
    strict_skill_whitelist: bool = Field(
        default=True,
        description="是否使用严格的技能白名单模式"
    )
    generate_manifest: bool = Field(
        default=True,
        description="是否生成 manifest.json"
    )

    model_config = {
        "extra": "forbid",
    }


class OpenClawAdapterInput(BaseModel):
    """
    OpenClaw 适配器输入模型

    包含 ExecutionPacket 和可选的适配器选项。
    """
    packet: ExecutionPacket = Field(..., description="执行包")
    options: AdapterOptions = Field(
        default_factory=AdapterOptions,
        description="适配器选项"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "OpenClawAdapterInput",
    "AdapterOptions",
]
