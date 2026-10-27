"""
Context Fragment Domain Model

Worker Profile Ingestion Baseline

定义上下文片段模型，用于表示从文件中读取的各类上下文内容。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ContextKind(str, Enum):
    """
    上下文类型枚举

    定义可能的上下文文件类型。
    """
    AGENT = "agent"          # AGENTS.md
    BOOT = "boot"            # BOOT.md
    HEARTBEAT = "heartbeat"  # HEARTBEAT.md
    SOUL = "soul"            # SOUL.md
    TOOLS = "tools"          # TOOLS.md
    RULES = "rules"          # RULES.md
    MEMORY = "memory"        # MEMORY.md
    USER = "user"            # USER.md
    # 扩展类型（用于动态内容）
    EXPERIENCE = "experience"  # 经验/经历
    SKILL = "skill"            # 技能
    EXPERTISE = "expertise"    # 专业领域
    OTHER = "other"            # 其他未识别类型


class ContextFragment(BaseModel):
    """
    上下文片段模型

    表示从文件中读取的一个上下文片段。

    Attributes:
        kind: 上下文类型
        filename: 文件名
        content: 原始内容（允许为空）
        source_path: 文件绝对路径
        weight: 权重（用于后续检索排序，0-1）
        metadata: 额外元数据

    注意：
        - content 可以为空，表示文件存在但内容为空
        - 与 MarkdownDocument 不同，允许空白内容
    """

    kind: ContextKind = Field(default=ContextKind.OTHER, description="上下文类型（已弃用，保留兼容性）")
    filename: str = Field(..., min_length=1, description="文件名")
    content: str = Field(default="", description="原始内容")
    source_path: str = Field(..., min_length=1, description="文件绝对路径")
    weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="权重，用于后续检索排序"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="额外元数据"
    )

    @property
    def is_empty(self) -> bool:
        """
        判断内容是否为空

        Returns:
            内容是否为空
        """
        return not self.content or not self.content.strip()

    @property
    def content_preview(self) -> str:
        """
        获取内容预览（最多 200 字符）

        Returns:
            截断后的内容预览
        """
        if not self.content:
            return ""
        return self.content[:200]

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "ContextKind",
    "ContextFragment",
]