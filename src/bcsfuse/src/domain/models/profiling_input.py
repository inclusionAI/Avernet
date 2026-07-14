"""
Profiling Input Domain Models

M2: Worker Profiling & Extraction

定义 Profiling 的输入模型，包括：
- MarkdownDocument: markdown 文档输入
- SkillMetadataInput: 技能元数据输入
- ResourceMetadataInput: 资源元数据输入
- ProfilingInput: 聚合输入
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class DocType(str, Enum):
    """文档类型枚举"""
    SOUL = "soul"
    RULES = "rules"
    MEMORY = "memory"


class SkillSource(str, Enum):
    """技能来源枚举"""
    WORKSPACE = "workspace"
    MANAGED = "managed"
    BUILTIN = "builtin"
    PLUGIN = "plugin"
    MCP = "mcp"


class TrustLevel(str, Enum):
    """信任级别枚举"""
    SANDBOX_ONLY = "sandbox_only"
    GUARDED = "guarded"
    TRUSTED = "trusted"


class ResourceKind(str, Enum):
    """资源类型枚举"""
    FILE = "file"
    FOLDER = "folder"
    DATASET = "dataset"
    API = "api"
    REPO = "repo"
    DASHBOARD = "dashboard"
    CREDENTIAL_HANDLE = "credential_handle"


class ResourceAccess(str, Enum):
    """资源访问权限枚举"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


class MarkdownDocument(BaseModel):
    """
    Markdown 文档输入模型

    表示一个待抽取的 markdown 文档（SOUL.md, RULES.md, MEMORY.md）。
    """

    doc_type: DocType = Field(..., description="文档类型")
    content: str = Field(..., min_length=1, description="文档内容")
    source_uri: Optional[str] = Field(None, description="文档来源 URI")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    @field_validator("content")
    @classmethod
    def content_must_not_be_whitespace_only(cls, v: str) -> str:
        """验证内容不能仅含空白"""
        if not v or not v.strip():
            raise ValueError("content cannot be empty or whitespace only")
        return v

    model_config = {
        "extra": "forbid",
    }


class SkillMetadataInput(BaseModel):
    """
    技能元数据输入模型

    表示一个待抽取的技能元数据。
    """

    name: str = Field(..., min_length=1, description="技能名称")
    source: SkillSource = Field(..., description="技能来源")
    description: Optional[str] = Field(None, description="描述")
    trust_level: TrustLevel = Field(..., description="信任级别")
    approval_required: bool = Field(default=False, description="是否需要审批")
    tool_names: list[str] = Field(default_factory=list, description="工具名称列表")

    model_config = {
        "extra": "forbid",
    }


class ResourceMetadataInput(BaseModel):
    """
    资源元数据输入模型

    表示一个待抽取的资源元数据。
    """

    id: str = Field(..., pattern=r"^res_[a-zA-Z0-9_-]+$", description="资源 ID")
    name: str = Field(..., min_length=1, description="资源名称")
    kind: ResourceKind = Field(..., description="资源类型")
    description: Optional[str] = Field(None, description="描述")
    uri: Optional[str] = Field(None, description="资源 URI")
    access: ResourceAccess = Field(..., description="访问权限")
    owner: Optional[str] = Field(None, description="所有者")
    tags: list[str] = Field(default_factory=list, description="标签")

    model_config = {
        "extra": "forbid",
    }


class ProfilingInput(BaseModel):
    """
    Profiling 聚合输入模型

    包含 Worker ID、文档列表、技能列表和资源列表。
    """

    worker_id: str = Field(
        ...,
        min_length=1,
        description="Worker ID"
    )
    documents: list[MarkdownDocument] = Field(
        ...,
        min_length=1,
        description="文档列表"
    )
    skills: list[SkillMetadataInput] = Field(
        default_factory=list,
        description="技能列表"
    )
    resources: list[ResourceMetadataInput] = Field(
        default_factory=list,
        description="资源列表"
    )

    def get_documents_by_type(self, doc_type: DocType) -> list[MarkdownDocument]:
        """
        按类型获取文档

        Args:
            doc_type: 文档类型

        Returns:
            指定类型的文档列表
        """
        return [doc for doc in self.documents if doc.doc_type == doc_type]

    def has_document_type(self, doc_type: DocType) -> bool:
        """
        检查是否有某类型文档

        Args:
            doc_type: 文档类型

        Returns:
            是否存在该类型的文档
        """
        return any(doc.doc_type == doc_type for doc in self.documents)

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    # 枚举
    "DocType",
    "SkillSource",
    "TrustLevel",
    "ResourceKind",
    "ResourceAccess",
    # 模型
    "MarkdownDocument",
    "SkillMetadataInput",
    "ResourceMetadataInput",
    "ProfilingInput",
]