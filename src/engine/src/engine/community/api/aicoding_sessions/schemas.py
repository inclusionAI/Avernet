"""Pydantic schemas for the AICoding sessions HTTP endpoints."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FileTreeNodeSchema(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None
    children: Optional[List["FileTreeNodeSchema"]] = None


class FileTreeResponse(BaseModel):
    success: bool = True
    session_id: str
    tree: List[FileTreeNodeSchema] = Field(default_factory=list)


class FilePreviewData(BaseModel):
    content: str
    size: int


class FilePreviewResponse(BaseModel):
    success: bool = True
    session_id: str
    data: FilePreviewData


class DiffTreeNodeSchema(BaseModel):
    name: str
    path: str
    is_dir: bool
    status: Optional[str] = None
    old_path: Optional[str] = None
    children: Optional[List["DiffTreeNodeSchema"]] = None


class GitProjectDiffSchema(BaseModel):
    project: str
    tree: DiffTreeNodeSchema


class GitDiffResponse(BaseModel):
    success: bool = True
    session_id: str
    diff_head: List[GitProjectDiffSchema] = Field(default_factory=list)


class FileDiffResponse(BaseModel):
    success: bool = True
    session_id: str
    project: str
    path: str
    diff: str


# ── API 4.1：sessions + run_status ────────────────────────────────────


class AicodingSessionSchema(BaseModel):
    """复用 engine/api/session/router.py:_session_to_dict 的字段集，并追加 run_status。"""

    id: str
    title: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    model: Optional[str] = None
    runtime: Optional[str] = None
    permission_mode: Optional[str] = None
    cwd: Optional[str] = None
    gmt_created: Optional[str] = None
    gmt_modified: Optional[str] = None
    message_count: int = 0
    last_message: Optional[dict] = None
    run_status: str = Field(
        "idle",
        description=(
            "活跃 run 的状态，原样透传 aix ``status.kind``"
            "（如 running / awaitingApproval / awaitingHuman / "
            "awaitingLlmEvaluation / completed / failed，未来 aix 新增的 kind 也会"
            "原样返回）；未启动 / 取不到活跃 run / 调用失败 / enrichment 超时一律为 ``idle``。"
        ),
    )
    ext_info: Optional[dict] = Field(
        None,
        description='扩展信息；各引擎按需填充工作项关联字段',
    )


class AicodingSessionListResponse(BaseModel):
    success: bool = True
    data: List[AicodingSessionSchema] = Field(default_factory=list)


# ── API 4.2：session 历史 runs ────────────────────────────────────────


class RunInfo(BaseModel):
    """对齐 aix run list --json 单条 run 的原生形态（透传，不重映射字段名）。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    projectDir: Optional[str] = Field(
        None,
        description="aix run list --filter 返回的 run 归属 project 目录绝对路径",
    )
    workflow: Optional[str] = None
    currentPhase: Optional[str] = None
    status: dict = Field(default_factory=dict, description='{"kind": "running"} 等')
    startedAtUnixMs: Optional[int] = None
    updatedAtUnixMs: Optional[int] = None
    endedAtUnixMs: Optional[int] = None
    phaseAttempts: Optional[dict] = None
    vars: Optional[dict] = None
    isActive: bool = False


class SessionRunsResponse(BaseModel):
    success: bool = True
    session_id: str
    runs: List[RunInfo] = Field(default_factory=list)


# ── API 4.3：phase 详情 ───────────────────────────────────────────────


class GateDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    index: int
    kind: str
    target: Optional[str] = None
    passed: bool
    optional: bool
    failedMessage: Optional[str] = None
    approval: Optional[dict] = None


class AgentActivity(BaseModel):
    # RFC 112：单个 agent 在某 phase 内的活动 rollup，权威来源是 aix 的
    # ``PhaseProgress.agents``（``aix run phase status --json -v``）。本层只透传，
    # 不二次计算。``extra="allow"`` 让 aix 后续给 agent 加细粒度字段（如 model /
    # cost）自动透传，无需再改本仓——契约真理源在 aix L0，ocb 是纯管道。
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    agentId: str
    # aix ``AgentLayer``（main / subagent / …），用 str 接以免 aix 新增枚举值时本层报错。
    agentKind: str
    agentToken: Optional[str] = None
    startedAtUnixMs: Optional[int] = None
    endedAtUnixMs: Optional[int] = None
    durationMs: Optional[int] = None
    toolCount: int = 0
    tokens: Optional[int] = None


class PhaseDetail(BaseModel):
    # ``extra="allow"``：aix 后续给 phase 加字段时自动透传，无需再提本仓 PR（同上）。
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    phase: str
    status: str
    attempts: int = 0
    startedAtUnixMs: Optional[int] = None
    completedAtUnixMs: Optional[int] = None
    durationMs: Optional[int] = None
    cycles: List[Any] = Field(default_factory=list)
    gates: List[GateDetail] = Field(default_factory=list)
    verifierResults: List[Any] = Field(default_factory=list)
    approvalStatus: Optional[dict] = None
    # 老 run / 无 fan-out 时 aix 输出空数组，故 default 为 []，字段恒在。
    agents: List[AgentActivity] = Field(default_factory=list)


class RunPhaseStatusData(BaseModel):
    """aix run phase status --verbose 返回结构。"""

    model_config = ConfigDict(populate_by_name=True)

    runId: str
    workflow: str
    currentPhase: str
    runStatus: dict = Field(default_factory=dict)
    startedAtUnixMs: Optional[int] = None
    updatedAtUnixMs: Optional[int] = None
    lastEventAtUnixMs: Optional[int] = None
    totalEvents: Optional[int] = None
    phases: Optional[List[PhaseDetail]] = None


class RunPhaseStatusResponse(BaseModel):
    success: bool = True
    session_id: str
    data: RunPhaseStatusData


# ── API 4.4：session pull-request outputs ────────────────────────────


class PullRequestOutputInfo(BaseModel):
    """aix run output list --kind pull-request 单条记录（透传 aix 原生字段）。"""

    model_config = ConfigDict(populate_by_name=True)

    run_id: Optional[str] = Field(None, alias="runId", description="关联的 Run ID")
    kind: str = Field(description="output 类型，固定为 'pull-request'")
    provider: Optional[str] = Field(None, description="代码托管平台，如 'antcode'")
    url: str = Field(description="PR 链接")
    title: Optional[str] = Field(None, description="PR 标题")
    at: Optional[int] = Field(None, description="生成时间 unix ms")
    project_dir: Optional[str] = Field(
        None, alias="projectDir", description="项目目录"
    )


class SessionPullRequestsResponse(BaseModel):
    success: bool = True
    session_id: str
    pull_requests: List[PullRequestOutputInfo] = Field(default_factory=list)


# ── API: session issue outputs ───────────────────────────────────────


class IssueOutputInfo(BaseModel):
    """aix run output list --kind issue 单条记录（透传 aix 原生字段）。"""

    model_config = ConfigDict(populate_by_name=True)

    run_id: Optional[str] = Field(None, alias="runId", description="关联的 Run ID")
    kind: str = Field(description="output 类型，固定为 'issue'")
    provider: Optional[str] = Field(None, description="工作项平台，如 'generic'")
    url: str = Field(description="Issue / 工作项链接")
    title: Optional[str] = Field(None, description="Issue / 工作项标题")
    at: Optional[int] = Field(None, description="生成时间 unix ms")
    project_dir: Optional[str] = Field(
        None, alias="projectDir", description="项目目录"
    )


class SessionIssuesResponse(BaseModel):
    success: bool = True
    session_id: str
    issues: List[IssueOutputInfo] = Field(default_factory=list)


# ── API: worktree status ─────────────────────────────────────────────


class WorktreeStatusResponse(BaseModel):
    success: bool = True
    session_id: str
    exists: bool = Field(description="Whether .worktree.json exists in workspace")
    status: str = Field("idle", description="idle/running/completed/failed")


__all__ = [
    "FileTreeNodeSchema",
    "FileTreeResponse",
    "FilePreviewData",
    "FilePreviewResponse",
    "DiffTreeNodeSchema",
    "GitProjectDiffSchema",
    "GitDiffResponse",
    "FileDiffResponse",
    "AicodingSessionSchema",
    "AicodingSessionListResponse",
    "RunInfo",
    "SessionRunsResponse",
    "GateDetail",
    "AgentActivity",
    "PhaseDetail",
    "RunPhaseStatusData",
    "RunPhaseStatusResponse",
    "PullRequestOutputInfo",
    "SessionPullRequestsResponse",
    "IssueOutputInfo",
    "SessionIssuesResponse",
    "WorktreeStatusResponse",
]
