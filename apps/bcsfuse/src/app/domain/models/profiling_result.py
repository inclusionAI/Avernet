"""
Profiling Result Domain Models

M2: Worker Profiling & Extraction

定义 Profiling 的输出模型，包括：
- SourceReference: 来源引用
- ExtractionWarning: 抽取警告
- ExtractionError: 抽取错误
- ExtractedCapability: 抽取的能力
- ExtractedDomain: 抽取的领域
- ExtractedResponsibility: 抽取的职责
- ExtractedConstraint: 抽取的约束
- ExtractedEscalationTrigger: 抽取的上报触发点
- ExtractedCollaborationStyle: 抽取的协作风格
- ExtractedSkill: 抽取的技能
- ExtractedResource: 抽取的资源
- ExtractedMemoryEpisode: 抽取的记忆片段
- WorkerProfileExtractionResult: 聚合抽取结果

注意：模型独立于 Worker 领域模型，因为：
- 抽取结果允许不完整
- 抽取结果需要 warnings/errors/source references
- 抽取结果是"候选画像"，不是最终 Worker 主数据
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# =============================================================================
# 枚举类型
# =============================================================================

class DocType(str, Enum):
    """文档类型枚举"""
    SOUL = "soul"
    RULES = "rules"
    MEMORY = "memory"


class CapabilityLevel(str, Enum):
    """能力级别枚举"""
    NOVICE = "novice"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class ConstraintPolicy(str, Enum):
    """约束策略枚举"""
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"
    APPROVAL_REQUIRED = "approval_required"


class ConstraintKind(str, Enum):
    """约束类型枚举"""
    POLICY = "policy"
    APPROVAL = "approval"
    SECURITY = "security"
    SCOPE = "scope"
    COST = "cost"


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


# =============================================================================
# 来源引用模型
# =============================================================================

class SourceReference(BaseModel):
    """
    来源引用模型

    用于追溯抽取结果的来源，必须至少包含一种定位信息。
    """

    doc_type: DocType = Field(..., description="文档类型")
    doc_name: str = Field(..., description="文档名称")
    section: Optional[str] = Field(None, description="文档节/段落名称")
    heading: Optional[str] = Field(None, description="文档标题")
    line_start: Optional[int] = Field(None, ge=1, description="起始行号")
    line_end: Optional[int] = Field(None, ge=1, description="结束行号")
    snippet: Optional[str] = Field(None, description="相关文本片段")

    @model_validator(mode="after")
    def validate_has_location(self) -> "SourceReference":
        """验证必须至少有一个定位信息"""
        has_location = any([
            self.section,
            self.heading,
            self.line_start is not None,
            self.line_end is not None,
        ])
        if not has_location:
            raise ValueError(
                "SourceReference must have at least one location field "
                "(section, heading, or line range)"
            )
        return self

    def __str__(self) -> str:
        """字符串表示"""
        parts = [self.doc_name]
        if self.section:
            parts.append(f"section={self.section}")
        if self.heading:
            parts.append(f"heading={self.heading}")
        if self.line_start is not None:
            if self.line_end:
                parts.append(f"lines={self.line_start}-{self.line_end}")
            else:
                parts.append(f"line={self.line_start}")
        return " -> ".join(parts)

    model_config = {
        "extra": "forbid",
    }


# =============================================================================
# 警告和错误模型
# =============================================================================

class ExtractionWarning(BaseModel):
    """
    抽取警告模型

    表示可部分成功继续的警告。
    """

    field: str = Field(..., description="相关字段")
    message: str = Field(..., description="警告消息")
    doc_type: DocType = Field(..., description="文档类型")
    doc_name: str = Field(..., description="文档名称")
    suggestion: Optional[str] = Field(None, description="改进建议")

    model_config = {
        "extra": "forbid",
    }


class ExtractionError(BaseModel):
    """
    抽取错误模型

    表示字段提取失败或输入不合法的错误。
    """

    field: str = Field(..., description="相关字段")
    message: str = Field(..., description="错误消息")
    doc_type: DocType = Field(..., description="文档类型")
    doc_name: str = Field(..., description="文档名称")
    severity: str = Field(default="medium", description="严重程度")

    model_config = {
        "extra": "forbid",
    }


# =============================================================================
# 抽取结果基类
# =============================================================================

class ExtractedItem(BaseModel):
    """抽取结果基类"""

    confidence: float = Field(..., ge=0, le=1, description="置信度")
    source_ref: SourceReference = Field(..., description="来源引用")

    model_config = {
        "extra": "forbid",
    }


# =============================================================================
# 具体抽取结果模型
# =============================================================================

class ExtractedCapability(ExtractedItem):
    """抽取的能力模型"""

    name: str = Field(..., description="能力名称")
    level: CapabilityLevel = Field(..., description="能力级别")


class ExtractedDomain(ExtractedItem):
    """抽取的领域模型"""

    name: str = Field(..., description="领域名称")


class ExtractedResponsibility(ExtractedItem):
    """抽取的职责模型"""

    description: str = Field(..., description="职责描述")


class ExtractedConstraint(ExtractedItem):
    """抽取的约束模型"""

    kind: ConstraintKind = Field(..., description="约束类型")
    rule: str = Field(..., description="约束规则描述")
    policy: ConstraintPolicy = Field(..., description="约束策略")
    severity: str = Field(default="medium", description="严重程度")


class ExtractedEscalationTrigger(ExtractedItem):
    """抽取的上报触发点模型"""

    condition: str = Field(..., description="触发条件")
    action: str = Field(..., description="触发动作")


class ExtractedCollaborationStyle(ExtractedItem):
    """抽取的协作风格模型"""

    preference: str = Field(..., description="偏好类型")
    details: Optional[str] = Field(None, description="详细说明")


class ExtractedSkill(ExtractedItem):
    """抽取的技能模型"""

    name: str = Field(..., description="技能名称")
    skill_source: SkillSource = Field(..., description="技能来源")
    trust_level: TrustLevel = Field(..., description="信任级别")
    approval_required: bool = Field(default=False, description="是否需要审批")


class ExtractedResource(ExtractedItem):
    """抽取的资源模型"""

    id: str = Field(..., pattern=r"^res_[a-zA-Z0-9_-]+$", description="资源 ID")
    name: str = Field(..., description="资源名称")
    kind: ResourceKind = Field(..., description="资源类型")
    access: ResourceAccess = Field(..., description="访问权限")


class ExtractedMemoryEpisode(ExtractedItem):
    """抽取的记忆片段模型"""

    timestamp: Optional[str] = Field(None, description="时间戳")
    summary: str = Field(..., description="摘要")
    task_type: Optional[str] = Field(None, description="任务类型")
    outcome: Optional[str] = Field(None, description="结果")


# =============================================================================
# 聚合抽取结果模型
# =============================================================================

class WorkerProfileExtractionResult(BaseModel):
    """
    Worker 画像抽取聚合结果模型

    包含所有抽取结果、警告和错误。
    """

    worker_id: str = Field(..., description="Worker ID")
    capabilities: list[ExtractedCapability] = Field(
        default_factory=list,
        description="能力列表"
    )
    domains: list[ExtractedDomain] = Field(
        default_factory=list,
        description="领域列表"
    )
    responsibilities: list[ExtractedResponsibility] = Field(
        default_factory=list,
        description="职责列表"
    )
    constraints: list[ExtractedConstraint] = Field(
        default_factory=list,
        description="约束列表"
    )
    escalation_triggers: list[ExtractedEscalationTrigger] = Field(
        default_factory=list,
        description="上报触发点列表"
    )
    collaboration_style: Optional[ExtractedCollaborationStyle] = Field(
        None,
        description="协作风格"
    )
    skills: list[ExtractedSkill] = Field(
        default_factory=list,
        description="技能列表"
    )
    resources: list[ExtractedResource] = Field(
        default_factory=list,
        description="资源列表"
    )
    memory_episodes: list[ExtractedMemoryEpisode] = Field(
        default_factory=list,
        description="记忆片段列表"
    )
    warnings: list[ExtractionWarning] = Field(
        default_factory=list,
        description="警告列表"
    )
    errors: list[ExtractionError] = Field(
        default_factory=list,
        description="错误列表"
    )

    def is_complete(self) -> bool:
        """
        判断抽取结果是否完整

        无错误时视为完整（警告不影响完整性）

        Returns:
            是否完整
        """
        return len(self.errors) == 0

    def get_summary(self) -> dict[str, Any]:
        """
        获取抽取结果摘要

        Returns:
            摘要字典
        """
        return {
            "capabilities_count": len(self.capabilities),
            "domains_count": len(self.domains),
            "responsibilities_count": len(self.responsibilities),
            "constraints_count": len(self.constraints),
            "escalation_triggers_count": len(self.escalation_triggers),
            "skills_count": len(self.skills),
            "resources_count": len(self.resources),
            "memory_episodes_count": len(self.memory_episodes),
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
        }

    def merge(self, other: "WorkerProfileExtractionResult") -> "WorkerProfileExtractionResult":
        """
        合并两个抽取结果

        Args:
            other: 另一个抽取结果

        Returns:
            合并后的抽取结果
        """
        return WorkerProfileExtractionResult(
            worker_id=self.worker_id,
            capabilities=self.capabilities + other.capabilities,
            domains=self.domains + other.domains,
            responsibilities=self.responsibilities + other.responsibilities,
            constraints=self.constraints + other.constraints,
            escalation_triggers=self.escalation_triggers + other.escalation_triggers,
            collaboration_style=other.collaboration_style or self.collaboration_style,
            skills=self.skills + other.skills,
            resources=self.resources + other.resources,
            memory_episodes=self.memory_episodes + other.memory_episodes,
            warnings=self.warnings + other.warnings,
            errors=self.errors + other.errors,
        )

    model_config = {
        "extra": "forbid",
    }


__all__ = [
    # 枚举
    "DocType",
    "CapabilityLevel",
    "ConstraintPolicy",
    "ConstraintKind",
    "SkillSource",
    "TrustLevel",
    "ResourceKind",
    "ResourceAccess",
    # 来源引用
    "SourceReference",
    # 警告和错误
    "ExtractionWarning",
    "ExtractionError",
    # 抽取结果基类
    "ExtractedItem",
    # 具体抽取结果
    "ExtractedCapability",
    "ExtractedDomain",
    "ExtractedResponsibility",
    "ExtractedConstraint",
    "ExtractedEscalationTrigger",
    "ExtractedCollaborationStyle",
    "ExtractedSkill",
    "ExtractedResource",
    "ExtractedMemoryEpisode",
    # 聚合结果
    "WorkerProfileExtractionResult",
]