"""
RetrievalInput Domain Model

M5: Unified Retrieval Fabric

检索输入模型，包含 TaskSpec、PlanDraft 和可选的过滤器/提示。

不包含真实检索逻辑，仅作为输入数据结构。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.task_spec import TaskSpec
from src.domain.models.plan_draft import PlanDraft


class RetrievalFilters(BaseModel):
    """
    检索过滤器

    用于约束检索结果的范围。
    只使用当前已存在且语义稳定的字段做过滤。

    Fields:
        worker_types: Worker 类型过滤 (human/bot)
        domains: 领域过滤
        trust_levels: 信任级别过滤 (sandbox_only/guarded/trusted)
        top_k: 每类候选的最大返回数量
    """
    worker_types: Optional[list[str]] = Field(None, description="Worker 类型过滤列表")
    domains: Optional[list[str]] = Field(None, description="领域过滤列表")
    trust_levels: Optional[list[str]] = Field(None, description="信任级别过滤列表")
    top_k: Optional[int] = Field(None, ge=1, description="每类候选最大返回数量")


class RetrievalHints(BaseModel):
    """
    检索提示

    用于引导检索结果的倾向性。不同于过滤器，提示不是硬性约束。

    Fields:
        preferred_worker_ids: 优先考虑的 Worker ID 列表
        preferred_skill_names: 优先考虑的技能名称列表
        preferred_resource_ids: 优先考虑的资源 ID 列表
        excluded_worker_ids: 排除的 Worker ID 列表
    """
    preferred_worker_ids: Optional[list[str]] = Field(None, description="优先考虑的 Worker ID")
    preferred_skill_names: Optional[list[str]] = Field(None, description="优先考虑的技能名称")
    preferred_resource_ids: Optional[list[str]] = Field(None, description="优先考虑的资源 ID")
    excluded_worker_ids: Optional[list[str]] = Field(None, description="排除的 Worker ID")


class RetrievalInput(BaseModel):
    """
    检索输入模型

    包含检索所需的所有输入数据。

    Fields:
        task_spec: 任务规格 (来自 M3)
        plan_draft: 计划草案 (来自 M4)
        filters: 可选的检索过滤器
        hints: 可选的检索提示
    """
    task_spec: TaskSpec = Field(..., description="任务规格")
    plan_draft: PlanDraft = Field(..., description="计划草案")
    filters: Optional[RetrievalFilters] = Field(None, description="检索过滤器")
    hints: Optional[RetrievalHints] = Field(None, description="检索提示")

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "RetrievalInput",
    "RetrievalFilters",
    "RetrievalHints",
]