"""Pydantic schemas for the AICoding skill-list HTTP endpoint."""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SkillPluginSchema(BaseModel):
    """仅 ``source=cc-plugin-cache`` 时存在；其余来源 skill 为 ``None``。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    version: Optional[str] = None
    marketplace: Optional[str] = None


class SkillInfoSchema(BaseModel):
    """单个 skill。``description`` 原样透传（部分 skill 文案不规范，如 ``"|"``）。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    source: Optional[str] = None
    description: Optional[str] = None
    path: Optional[str] = None
    plugin: Optional[SkillPluginSchema] = None


class BackendSkillsSchema(BaseModel):
    """每个 backend 节点只保留 ``skills``，丢弃 aix 可能返回的其它杂字段。"""

    model_config = ConfigDict(extra="ignore")

    skills: List[SkillInfoSchema] = Field(default_factory=list)


class SkillListResponse(BaseModel):
    success: bool = True
    backends: Dict[str, BackendSkillsSchema] = Field(default_factory=dict)
