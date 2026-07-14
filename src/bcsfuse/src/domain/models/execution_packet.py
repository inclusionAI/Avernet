"""
ExecutionPacket Domain Model

执行包模型，与 schemas/ExecutionPacket.json 对齐。

M0 骨架实现，M8 会完善。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.models.task_spec import TaskSpec
from src.domain.models.plan_draft import PlanDraft
from src.domain.models.team_spec import TeamSpec
from src.domain.models.candidate_bundle import KnowledgeItem
from src.domain.models.worker import SkillRef, ResourceRef


class ContextPack(BaseModel):
    """上下文包"""
    summary: str = Field(..., description="摘要")
    knowledge_items: list[KnowledgeItem] = Field(default_factory=list, description="知识项")
    memory_injections: list[str] = Field(default_factory=list, description="记忆注入")
    citations: list[str] = Field(default_factory=list, description="引用")

    model_config = {
        "extra": "forbid",
    }


class ResourcePack(BaseModel):
    """资源包"""
    resources: list[ResourceRef] = Field(default_factory=list, description="资源列表")
    mount_instructions: list[str] = Field(default_factory=list, description="挂载说明")

    model_config = {
        "extra": "forbid",
    }


class SkillPack(BaseModel):
    """技能包"""
    skills: list[SkillRef] = Field(default_factory=list, description="技能列表")
    allowlist: list[str] = Field(default_factory=list, description="白名单")
    sandbox_required: bool = Field(..., description="是否需要沙箱")

    model_config = {
        "extra": "forbid",
    }


class Guardrails(BaseModel):
    """护栏规则"""
    rules: list[str] = Field(default_factory=list, description="规则列表")
    approvals: list[str] = Field(default_factory=list, description="需要审批的动作")
    blocked_actions: list[str] = Field(default_factory=list, description="禁止的动作")

    model_config = {
        "extra": "forbid",
    }


class OutputContract(BaseModel):
    """输出契约"""
    required_artifacts: list[str] = Field(default_factory=list, description="必需工件")
    required_sections: list[str] = Field(default_factory=list, description="必需章节")
    must_include_validation: bool = Field(..., description="是否必须包含验证")
    format_hints: list[str] = Field(default_factory=list, description="格式提示")

    model_config = {
        "extra": "forbid",
    }


class ExecutionPacket(BaseModel):
    """
    执行包模型

    对应 JSON Schema: schemas/ExecutionPacket.json
    """
    task_spec: TaskSpec = Field(..., description="任务规格")
    plan_draft: PlanDraft = Field(..., description="计划草案")
    team_spec: TeamSpec = Field(..., description="团队规格")
    context_pack: ContextPack = Field(..., description="上下文包")
    resource_pack: ResourcePack = Field(..., description="资源包")
    skill_pack: SkillPack = Field(..., description="技能包")
    guardrails: Guardrails = Field(..., description="护栏规则")
    output_contract: OutputContract = Field(..., description="输出契约")
    launch_prompt: str = Field(..., min_length=1, description="启动提示词")

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "ExecutionPacket",
    "ContextPack",
    "ResourcePack",
    "SkillPack",
    "Guardrails",
    "OutputContract",
]