"""
CandidateBundle Domain Model

候选集模型，与 schemas/CandidateBundle.json 对齐。

M0 骨架实现，M5 会完善。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.models.worker import Worker, SkillRef, ResourceRef


class KnowledgeItem(BaseModel):
    """知识项"""
    id: str = Field(..., pattern=r"^kno_[a-zA-Z0-9_-]+$", description="知识项 ID")
    kind: str = Field(..., description="知识类型")
    title: str = Field(..., description="标题")
    summary: str = Field(..., description="摘要")
    source_uri: str | None = Field(None, description="来源 URI")
    highlights: list[str] = Field(default_factory=list, description="高亮片段")
    freshness: str = Field(..., description="新鲜度")
    reliability: str = Field(..., description="可靠性")
    tags: list[str] = Field(default_factory=list, description="标签")


class CandidateBundle(BaseModel):
    """
    候选集模型

    对应 JSON Schema: schemas/CandidateBundle.json
    """
    workers: list[Worker] = Field(default_factory=list, description="候选 Worker 列表")
    knowledge_items: list[KnowledgeItem] = Field(default_factory=list, description="知识项列表")
    skills: list[SkillRef] = Field(default_factory=list, description="技能列表")
    resources: list[ResourceRef] = Field(default_factory=list, description="资源列表")
    evidence: list[str] = Field(default_factory=list, description="证据引用")

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "CandidateBundle",
    "KnowledgeItem",
]