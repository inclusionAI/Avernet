"""
Worker Domain Model

Worker 主档案模型，与 schemas/Worker.json 对齐。

字段说明：
- id: Worker 唯一标识，格式为 wrk_*
- type: Worker 类型，human 或 bot
- identity: 身份信息
- responsibilities: 职责列表
- domains: 领域标签
- capabilities: 能力列表
- constraints: 约束条件
- skills: 技能引用
- resources: 资源引用
- memory_refs: 记忆引用
- state: 运行态
- performance_stats: 性能统计
- lifecycle_state: 生命周期状态（Stage 1 新增）
- source_type: 来源类型（Stage 1 新增）
- source_ref: 来源引用（Stage 1 新增）
- external_id: 外部 ID（Stage 1 新增）
- created_at: 创建时间（Stage 1 新增）
- updated_at: 更新时间（Stage 1 新增）
- created_by: 创建者（Stage 1 新增）
- updated_by: 更新者（Stage 1 新增）
- active_profile_key: 活跃 profile 引用（Stage 1 新增）
- version: 版本号（乐观锁，Stage 1 新增）
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from src.domain.models.worker_config import WorkerConfig
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType


class WorkerType(str, Enum):
    """Worker 类型枚举"""
    HUMAN = "human"
    BOT = "bot"


class CapabilityLevel(str, Enum):
    """能力级别枚举"""
    NOVICE = "novice"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class ConstraintKind(str, Enum):
    """约束类型枚举"""
    POLICY = "policy"
    APPROVAL = "approval"
    SECURITY = "security"
    SCOPE = "scope"
    COST = "cost"


class ConstraintSeverity(str, Enum):
    """约束严重程度枚举"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Availability(str, Enum):
    """可见性状态枚举"""
    PRIVATE = "private"
    PROTECTED = "protected"
    PUBLIC = "public"


class TrustLevel(str, Enum):
    """信任级别枚举"""
    UNVERIFIED = "unverified"
    VERIFYING = "verifying"
    SANDBOX_ONLY = "sandbox_only"
    GUARDED = "guarded"
    TRUSTED = "trusted"


class SkillSource(str, Enum):
    """技能来源枚举"""
    WORKSPACE = "workspace"
    MANAGED = "managed"
    BUILTIN = "builtin"
    PLUGIN = "plugin"
    MCP = "mcp"


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


class WorkerIdentity(BaseModel):
    """Worker 身份信息"""
    name: str = Field(..., min_length=1, description="Worker 名称")
    handle: str = Field(..., min_length=1, description="Worker 句柄/用户名")
    title: Optional[str] = Field(None, description="职位头衔")
    owner_team: Optional[str] = Field(None, description="所属团队")
    description: Optional[str] = Field(None, description="描述")


class Capability(BaseModel):
    """能力描述"""
    name: str = Field(..., description="能力名称")
    level: CapabilityLevel = Field(..., description="能力级别")
    evidence_refs: list[str] = Field(default_factory=list, description="证据引用")


class Constraint(BaseModel):
    """约束条件"""
    kind: ConstraintKind = Field(..., description="约束类型")
    rule: str = Field(..., description="约束规则描述")
    severity: ConstraintSeverity = Field(
        default=ConstraintSeverity.MEDIUM,
        description="严重程度"
    )


class SkillRef(BaseModel):
    """技能引用"""
    name: str = Field(..., description="技能名称")
    source: SkillSource = Field(..., description="技能来源")
    description: Optional[str] = Field(None, description="描述")
    trust_level: TrustLevel = Field(..., description="信任级别")
    approval_required: bool = Field(default=False, description="是否需要审批")
    openclaw_skill_path: Optional[str] = Field(None, description="OpenClaw 技能路径")
    tool_names: list[str] = Field(default_factory=list, description="工具名称列表")


class ResourceRef(BaseModel):
    """资源引用"""
    id: str = Field(..., pattern=r"^res_[a-zA-Z0-9_-]+$", description="资源 ID")
    kind: ResourceKind = Field(..., description="资源类型")
    name: str = Field(..., description="资源名称")
    description: Optional[str] = Field(None, description="描述")
    uri: Optional[str] = Field(None, description="资源 URI")
    access: ResourceAccess = Field(..., description="访问权限")
    owner: Optional[str] = Field(None, description="所有者")
    tags: list[str] = Field(default_factory=list, description="标签")


class WorkerState(BaseModel):
    """
    Worker 运行态

    Stage 1 增强：增加明确的 runtime_state 字段

    原有字段保留（兼容性）：
    - availability: 可用性状态
    - trust_level: 信任级别
    - current_load: 当前负载
    - last_seen_at: 最后在线时间

    新增字段：
    - runtime_state: 运行态（online/offline）
    - runtime_state_updated_at: 运行态更新时间
    - runtime_state_updated_by: 运行态更新来源
    """
    availability: Availability = Field(..., description="可用性状态")
    trust_level: TrustLevel = Field(..., description="信任级别")
    current_load: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="当前负载 (0-1)"
    )
    last_seen_at: Optional[datetime] = Field(None, description="最后在线时间")

    # Stage 1 新增：运行态
    runtime_state: WorkerRuntimeState = Field(
        default=WorkerRuntimeState.OFFLINE,
        description="运行态（online/offline）"
    )
    runtime_state_updated_at: Optional[datetime] = Field(
        None,
        description="运行态更新时间"
    )
    runtime_state_updated_by: Optional[str] = Field(
        None,
        description="运行态更新来源"
    )


class PerformanceStats(BaseModel):
    """性能统计"""
    success_rate_30d: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="30 天成功率"
    )
    task_count_30d: Optional[int] = Field(
        None,
        ge=0,
        description="30 天任务数"
    )


class Worker(BaseModel):
    """
    Worker 主档案模型

    对应 JSON Schema: schemas/Worker.json

    Stage 1 增强：
    - 增加生命周期状态（lifecycle_state）
    - 增加来源信息（source_type, source_ref, external_id）
    - 增加管理元数据（created_at, updated_at, created_by, updated_by）
    - 增加活跃 profile 引用（active_profile_key）
    - 增加版本号（version，用于乐观锁）
    """
    id: str = Field(
        ...,
        min_length=1,
        description="Worker 唯一标识"
    )
    type: WorkerType = Field(..., description="Worker 类型")
    identity: WorkerIdentity = Field(..., description="身份信息")
    responsibilities: list[str] = Field(
        ...,
        description="职责列表"
    )
    domains: list[str] = Field(default_factory=list, description="领域标签")
    capabilities: list[Capability] = Field(
        ...,
        min_length=0,
        description="能力列表"
    )
    constraints: list[Constraint] = Field(
        default_factory=list,
        description="约束条件"
    )
    skills: list[SkillRef] = Field(
        default_factory=list,
        description="技能引用"
    )
    resources: list[ResourceRef] = Field(
        default_factory=list,
        description="资源引用"
    )
    memory_refs: list[str] = Field(
        default_factory=list,
        description="记忆引用"
    )
    state: WorkerState = Field(..., description="运行态")
    performance_stats: PerformanceStats = Field(
        default_factory=PerformanceStats,
        description="性能统计"
    )

    # Stage 1 新增：生命周期状态
    lifecycle_state: WorkerLifecycleState = Field(
        default=WorkerLifecycleState.ACTIVE,
        description="生命周期状态"
    )

    # Stage 1 新增：来源信息
    source_type: WorkerSourceType = Field(
        default=WorkerSourceType.API,
        description="来源类型"
    )
    source_ref: Optional[str] = Field(
        None,
        description="来源引用"
    )
    external_id: Optional[str] = Field(
        None,
        description="外部 ID"
    )

    # Stage 1 新增：管理元数据
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="创建时间"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="更新时间"
    )
    created_by: Optional[str] = Field(
        None,
        description="创建者"
    )
    updated_by: Optional[str] = Field(
        None,
        description="更新者"
    )

    # Stage 1 新增：活跃 profile 引用
    active_profile_key: Optional[str] = Field(
        None,
        description="活跃 Profile 引用"
    )

    # Worker 行为配置（存储于 config JSON 列）
    config: WorkerConfig = Field(
        default_factory=WorkerConfig,
        description="Worker 行为配置",
    )

    # Stage 1 新增：版本号（乐观锁）
    version: int = Field(
        default=1,
        ge=1,
        description="版本号"
    )

    model_config = {
        "extra": "forbid",  # 禁止额外字段
        "use_enum_values": True,  # 枚举使用值而非枚举对象
    }


__all__ = [
    "Worker",
    "WorkerType",
    "WorkerIdentity",
    "Capability",
    "CapabilityLevel",
    "Constraint",
    "ConstraintKind",
    "ConstraintSeverity",
    "SkillRef",
    "SkillSource",
    "ResourceRef",
    "ResourceKind",
    "ResourceAccess",
    "WorkerState",
    "Availability",
    "TrustLevel",
    "PerformanceStats",
    # Stage 1 新增导出
    "WorkerLifecycleState",
    "WorkerRuntimeState",
    "WorkerSourceType",
    # Worker Config
    "WorkerConfig",
]