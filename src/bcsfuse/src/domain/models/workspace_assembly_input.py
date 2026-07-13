"""
WorkspaceAssemblyInput Domain Model

M7: Workspace / Group Assembly

工作空间组装输入模型，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle 和提示。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.domain.models.task_spec import TaskSpec
from src.domain.models.plan_draft import PlanDraft
from src.domain.models.team_spec import TeamSpec
from src.domain.models.candidate_bundle import CandidateBundle


class AssemblyHints(BaseModel):
    """
    组装提示

    定义工作空间组装的可选配置。
    """
    include_all_knowledge: bool = Field(
        default=True,
        description="是否包含所有知识项"
    )
    include_all_resources: bool = Field(
        default=True,
        description="是否包含所有资源"
    )
    generate_initial_threads: bool = Field(
        default=False,
        description="是否生成初始线程（M7 baseline 不实现）"
    )
    custom_mount_paths: dict[str, str] = Field(
        default_factory=dict,
        description="自定义挂载路径 {resource_id: mount_path}"
    )

    model_config = {
        "extra": "forbid",
    }


class WorkspaceAssemblyInput(BaseModel):
    """
    工作空间组装输入

    包含进行工作空间组装所需的所有输入数据：
    - TaskSpec: 任务规格
    - PlanDraft: 规划草案
    - TeamSpec: 团队规格
    - CandidateBundle: 候选集（包含 knowledge/resource）
    - AssemblyHints: 组装提示
    """
    task_spec: TaskSpec = Field(..., description="任务规格")
    plan_draft: PlanDraft = Field(..., description="规划草案")
    team_spec: TeamSpec = Field(..., description="团队规格")
    candidate_bundle: CandidateBundle = Field(..., description="候选集")
    hints: AssemblyHints = Field(
        default_factory=AssemblyHints,
        description="组装提示"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "WorkspaceAssemblyInput",
    "AssemblyHints",
]