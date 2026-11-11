"""
TeamSpec Domain Model

团队规格模型，与 schemas/TeamSpec.json 对齐。

M0 骨架实现，M6 会完善。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoleAssignment(BaseModel):
    """角色分配"""
    worker_id: str = Field(..., description="Worker ID")
    role: str = Field(..., description="角色")
    objective: str = Field(..., description="目标")

    model_config = {
        "extra": "forbid",
    }


class TeamSpec(BaseModel):
    """
    团队规格模型

    对应 JSON Schema: schemas/TeamSpec.json
    """
    team_id: str = Field(
        ...,
        pattern=r"^team_[a-zA-Z0-9_-]+$",
        description="团队唯一标识"
    )
    members: list[str] = Field(..., min_length=1, description="成员 ID 列表")
    role_assignments: list[RoleAssignment] = Field(..., min_length=1, description="角色分配")
    selected_skills: list[str] = Field(default_factory=list, description="选中的技能")
    selected_resources: list[str] = Field(default_factory=list, description="选中的资源")
    composition_rationale: list[str] = Field(..., min_length=1, description="组成理由")
    gaps: list[str] = Field(default_factory=list, description="缺口")

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    "TeamSpec",
    "RoleAssignment",
]