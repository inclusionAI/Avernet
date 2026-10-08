"""
WorkspaceAssemblyResult Domain Model

M7: Workspace / Group Assembly

工作空间组装结果模型，包含 Workspace、警告、错误、解释和挂载信息。
"""

from __future__ import annotations

from typing import Optional, Any

from pydantic import BaseModel, Field, computed_field

from src.domain.models.workspace import Workspace


class MountInfo(BaseModel):
    """
    挂载信息

    记录知识或资源的挂载详情。
    """
    id: str = Field(..., description="挂载对象 ID")
    type: str = Field(..., description="类型: knowledge / resource")
    mount_reason: Optional[str] = Field(
        default=None,
        description="挂载原因"
    )
    custom_path: Optional[str] = Field(
        default=None,
        description="自定义挂载路径"
    )

    model_config = {
        "extra": "forbid",
    }


class AssemblyExplanation(BaseModel):
    """
    组装解释

    说明组装过程中的决策。
    """
    subject: str = Field(..., description="解释主题")
    description: str = Field(..., description="解释描述")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    model_config = {
        "extra": "forbid",
    }


class AssemblyWarning(BaseModel):
    """
    组装警告

    表示非致命问题。
    """
    code: str = Field(..., description="警告代码")
    message: str = Field(..., description="警告消息")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    model_config = {
        "extra": "forbid",
    }


class AssemblyError(BaseModel):
    """
    组装错误

    表示致命问题。
    """
    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误消息")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息"
    )

    model_config = {
        "extra": "forbid",
    }


class WorkspaceAssemblyResult(BaseModel):
    """
    工作空间组装结果

    包含组装出的 Workspace、警告、错误、解释和挂载信息。
    """
    workspace: Optional[Workspace] = Field(
        default=None,
        description="组装出的工作空间"
    )
    warnings: list[AssemblyWarning] = Field(
        default_factory=list,
        description="警告列表"
    )
    errors: list[AssemblyError] = Field(
        default_factory=list,
        description="错误列表"
    )
    explanations: list[AssemblyExplanation] = Field(
        default_factory=list,
        description="解释列表"
    )
    mount_info: list[MountInfo] = Field(
        default_factory=list,
        description="挂载信息列表"
    )

    model_config = {
        "extra": "forbid",
    }

    @computed_field
    @property
    def is_success(self) -> bool:
        """判断组装是否成功"""
        return self.workspace is not None and len(self.errors) == 0


__all__ = [
    "WorkspaceAssemblyResult",
    "AssemblyExplanation",
    "AssemblyWarning",
    "AssemblyError",
    "MountInfo",
]