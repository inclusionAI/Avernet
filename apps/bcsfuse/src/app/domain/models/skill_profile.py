"""
Skill Profile Domain Model

Worker Profile Ingestion Baseline

定义归一化后的当前技能条目模型。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SkillProfile(BaseModel):
    """
    技能档案模型

    表示归一化后的当前技能条目。

    注意：这是从 skill_sets.json 中 is_current=true 的技能组
    提取的已激活技能条目，不是完整的 SkillDefinition/SkillActivation 双层模型。

    未来如需完整建模技能定义和激活关系，应单独设计。

    Attributes:
        name: 技能名称
        description: 技能描述（可选）
        skill_id: 技能 ID（来自 skill 字段）
        path: 技能路径（可选）
        skill_set_name: 所属技能组名称
        is_active: 是否激活（默认 True）
        metadata: 额外元数据
    """

    name: str = Field(..., min_length=1, description="技能名称")
    description: Optional[str] = Field(None, description="技能描述")
    skill_id: str = Field(..., min_length=1, description="技能 ID（来自 skill 字段）")
    path: Optional[str] = Field(None, description="技能路径")
    skill_set_name: str = Field(..., min_length=1, description="所属技能组名称")
    is_active: bool = Field(default=True, description="是否激活")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="额外元数据"
    )

    @property
    def display_name(self) -> str:
        """
        获取显示名称

        格式: "name: description" 或 "name"

        Returns:
            显示名称
        """
        if self.description:
            return f"{self.name}: {self.description}"
        return self.name

    @property
    def searchable_text(self) -> str:
        """
        获取可检索文本

        Returns:
            用于检索的文本
        """
        parts = [self.name]
        if self.description:
            parts.append(self.description)
        return " ".join(parts)

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "SkillProfile",
]