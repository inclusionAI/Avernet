"""
AICODING bindings API contracts — Pydantic models.

Used by api/aicoding/router.py. Field shapes intentionally mirror identity
BotIdentity* models so the frontend can share TypeScript interfaces.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Bindings Schemas ───────────────────────────────────────────────────────


class AiCodingBindingsContent(BaseModel):
    """PUT request body."""
    content: str = Field(..., description="Bindings JSON as a string")


class AiCodingBindingsResponse(BaseModel):
    """GET response."""
    success: bool
    file_type: str      # always "aicoding.bindings.json", kept for parity with identity
    entity_type: str
    entity_id: str
    bot_id: str
    content: str
    file_path: str


class AiCodingBindingsUpdateResponse(BaseModel):
    """PUT response."""
    success: bool
    message: str
    file_type: str      # always "aicoding.bindings.json", kept for parity with identity
    entity_type: str
    entity_id: str
    bot_id: str
    file_path: str


# ── Workspace Schemas ───────────────────────────────────────────────────────


class InitializeWorkspaceRequest(BaseModel):
    """初始化工作区请求（含可选 Git 克隆）

    工作区路径: {bolt_base}/{entity_type}_{entity_id}/{bot_id}/aicoding{path}
    path 必须以 /workspace 开头
    """

    bot_id: str = Field(..., description="Bot ID")
    path: str = Field(
        default="/workspace",
        description="工作区相对路径，必须以 /workspace 开头"
    )
    entity_type: Optional[str] = Field(
        default="staff",
        description="实体类型 (staff/team/project)"
    )
    git_url: Optional[str] = Field(
        default=None,
        description="AntCode HTTPS 地址（选填），填写后会自动克隆到工作区"
    )
    branch: Optional[str] = Field(
        default=None,
        description="克隆分支（可选）"
    )


class RepoInfo(BaseModel):
    language: str = Field(..., description="Detected primary language")
    path: str = Field(..., description="Subdirectory path (e.g., './web')")
    dependency_status: str = Field(
        "unknown", description="Dependency install status: installed, missing, unknown"
    )


class GitInfo(BaseModel):
    branch: Optional[str] = Field(None, description="Current git branch")
    status: Optional[str] = Field(None, description="Git working tree status")
    remote_url: Optional[str] = Field(None, description="Git remote URL")


class InitializeWorkspaceResponse(BaseModel):
    """初始化工作区响应"""

    workspace_id: str = Field(..., description="工作区 ID")
    path: str = Field(..., description="工作区路径")
    name: str = Field(..., description="工作区名称")
    repos: List[RepoInfo] = Field(default_factory=list, description="检测到的代码仓库")
    git: Optional[GitInfo] = Field(None, description="Git 状态信息")
    warnings: List[str] = Field(default_factory=list, description="警告信息")
    ready: bool = Field(True, description="工作区是否可用")


# ── Workflow Catalog Schemas ───────────────────────────────────────────────


class WorkflowItem(BaseModel):
    """单个工作流的展示信息（列表项 + hover 详情）。"""

    name: str = Field(..., description="工作流名称（仓库内末级目录名，如 spec-to-pr）")
    description: str = Field("", description="workflow.yaml 中的 description 字段")
    category: str = Field("", description="一级分类：business / infra")
    domain: str = Field("", description="二级业务域 slug：aml / content 等")
    domain_name: str = Field(
        "",
        description=(
            "二级业务域中文名（来自 manifest.json 的 domains 映射）；"
            "manifest 缺失或无对应中文名时回退为 domain slug 本身"
        ),
    )
    path: str = Field(..., description="workflow.yaml 在仓库内的相对路径")
    tags: List[str] = Field(
        default_factory=list,
        description=(
            "标签（技术栈 / 用途等），来自 workflow.yaml 头部；缺失时为空列表。"
            "aix 引擎忽略未知字段，故 tags 放在 yaml 头部不影响引擎解析"
        ),
    )


class WorkflowListResponse(BaseModel):
    """工作流列表接口响应。"""

    success: bool = Field(True, description="是否成功")
    data: List[WorkflowItem] = Field(default_factory=list, description="工作流列表")


# ── CodeFuse Token Schemas ─────────────────────────────────────────────────


class CodefuseTokenRequest(BaseModel):
    """PUT /api/aicoding/bots/{bot_id}/codefuse/auth request body.

    ``token`` field is a base64-encoded auth_code returned by CodeFuse SSO
    callback.  Decoded it yields JSON ``{"t":"<codexmuse_token>","w":"<workid>"}``.
    """

    token: str = Field(..., description="Base64-encoded CodeFuse auth_code")
