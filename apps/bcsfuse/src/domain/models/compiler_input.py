"""
CompilerInput Domain Model

M8: Execution Packet Compiler

编译器输入模型，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle、Workspace 和可选提示。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.task_spec import TaskSpec
from src.domain.models.plan_draft import PlanDraft
from src.domain.models.team_spec import TeamSpec
from src.domain.models.candidate_bundle import CandidateBundle
from src.domain.models.workspace import Workspace


class CompilerHints(BaseModel):
    """
    编译器提示

    控制编译行为的可选参数。
    """
    include_full_context: bool = Field(
        default=True,
        description="是否包含完整上下文（False 时进行裁剪）"
    )
    generate_memory_summary: bool = Field(
        default=True,
        description="是否生成记忆摘要"
    )
    strict_guardrails: bool = Field(
        default=True,
        description="是否生成严格护栏规则"
    )
    max_context_items: Optional[int] = Field(
        default=None,
        description="最大上下文项数（None 表示无限制）"
    )

    model_config = {
        "extra": "forbid",
    }


class CompilerInput(BaseModel):
    """
    编译器输入模型

    包含编译 ExecutionPacket 所需的所有输入。
    """
    task_spec: TaskSpec = Field(..., description="任务规格")
    plan_draft: PlanDraft = Field(..., description="规划草案")
    team_spec: TeamSpec = Field(..., description="团队规格")
    candidate_bundle: CandidateBundle = Field(..., description="候选集")
    workspace: Workspace = Field(..., description="工作空间")
    hints: CompilerHints = Field(
        default_factory=CompilerHints,
        description="编译器提示"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "CompilerInput",
    "CompilerHints",
]