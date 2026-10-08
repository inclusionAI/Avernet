"""
HandoffBundle Domain Model

M9: OpenClaw Adapter

Handoff bundle 模型，包含 OpenClaw 工作区文件和 manifest。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HandoffFile(BaseModel):
    """
    Handoff 文件

    表示一个将要写入 OpenClaw 工作区的文件。
    """
    filename: str = Field(..., description="文件名")
    content: str = Field(..., description="文件内容")
    content_type: str = Field(
        default="markdown",
        description="内容类型"
    )

    model_config = {
        "extra": "forbid",
    }


class Manifest(BaseModel):
    """
    Manifest 文件结构

    记录 handoff bundle 的元信息。
    """
    task_id: str = Field(..., description="任务 ID")
    team_id: Optional[str] = Field(default=None, description="团队 ID")
    generated_at: str = Field(..., description="生成时间")
    files: list[str] = Field(
        default_factory=list,
        description="文件列表"
    )
    skills_enabled: list[str] = Field(
        default_factory=list,
        description="启用的技能白名单"
    )
    resources_enabled: list[str] = Field(
        default_factory=list,
        description="启用的资源列表"
    )

    model_config = {
        "extra": "forbid",
    }


class HandoffBundle(BaseModel):
    """
    Handoff Bundle

    包含 OpenClaw 工作区所需的所有文件和 manifest。
    """
    files: list[HandoffFile] = Field(
        default_factory=list,
        description="文件列表"
    )
    manifest: Manifest = Field(..., description="Manifest")

    model_config = {
        "extra": "forbid",
    }

    def get_file(self, filename: str) -> Optional[HandoffFile]:
        """
        获取指定文件名的文件

        Args:
            filename: 文件名

        Returns:
            找到的文件，或 None
        """
        for file in self.files:
            if file.filename == filename:
                return file
        return None

    def has_file(self, filename: str) -> bool:
        """
        检查是否存在指定文件

        Args:
            filename: 文件名

        Returns:
            是否存在
        """
        return self.get_file(filename) is not None

    @property
    def file_names(self) -> list[str]:
        """获取所有文件名"""
        return [f.filename for f in self.files]


__all__ = [
    "HandoffBundle",
    "HandoffFile",
    "Manifest",
]