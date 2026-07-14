"""
FusionResult

G1: Fusion Entry Layer / G2: Conflict Alignment Layer / G5: Expert Diagnosis Layer

融合结果的领域模型定义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Forward reference for structured_risk to avoid circular import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.models.structured_risk_assessment import StructuredRiskAssessment


class Perspective(BaseModel):
    """
    参与者视角

    单个参与者对问题的视角和评估。

    G1 核心字段：
    - participant_id, participant_type, role, summary, confidence, evidence, status

    G2 扩展字段：
    - key_points: 核心依据点
    - concerns: 主要顾虑
    - flexibility: 可妥协/折中点

    G5 扩展字段：
    - role 支持 "expert"
    """

    model_config = {"extra": "forbid"}

    participant_id: str = Field(
        description="参与者标识符",
    )
    participant_type: Literal["bot", "human", "system"] = Field(
        description="参与者类型",
    )
    role: Literal["driver", "consultant", "observer", "expert"] = Field(
        description="参与者在本次融合中的角色",
    )
    summary: str = Field(
        description="视角摘要",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="置信度 (0-1)",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="支持该视角的证据",
    )
    status: Literal["completed", "timed_out", "failed", "skipped"] = Field(
        description="视角收集状态",
    )
    # G2 扩展字段
    key_points: list[str] = Field(
        default_factory=list,
        description="核心依据点（G2）",
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="主要顾虑（G2）",
    )
    flexibility: Optional[str] = Field(
        default=None,
        max_length=500,
        description="可妥协/折中点（G2）",
    )
    # Phase D2: Metadata for diagnostics (profile loading, format conversion, etc.)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata 包含 profile 加载诊断信息",
    )


class Recommendation(BaseModel):
    """
    融合建议

    基于多视角融合生成的综合建议。
    """

    model_config = {"extra": "forbid"}

    summary: str = Field(
        description="建议摘要",
    )
    decision: Literal["yes", "no", "conditional_yes", "needs_more_information"] = Field(
        description="决策结论",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="识别的风险",
    )
    next_actions: list[str] = Field(
        default_factory=list,
        description="建议的下一步行动",
    )


class FusionTiming(BaseModel):
    """
    融合计时信息

    记录融合操作的时间信息。
    """

    model_config = {"extra": "forbid"}

    started_at: datetime = Field(
        description="开始时间",
    )
    finished_at: datetime = Field(
        description="完成时间",
    )
    duration_ms: int = Field(
        ge=0,
        description="耗时（毫秒）",
    )


class FusionResult(BaseModel):
    """
    融合结果

    多参与者视角融合的最终结果。

    G1 核心字段：
    - group_id, fusion_id, question, driver_bot_id
    - perspectives, recommendation
    - partial_success, warnings, errors, timing

    G2 扩展字段：
    - fusion_mode: 融合模式标识
    - conflicts: 识别的冲突列表
    - alignment_points: 达成的对齐点列表
    - key_insights: 关键洞察列表

    G5 扩展字段：
    - risk_assessment: 风险评估
    - critical_issues: 关键问题列表
    - recommendations: 专家建议列表（多个行动项）
    - go_live_conditions: 上线条件列表
    - summary: 诊断摘要
    """

    model_config = {"extra": "forbid"}

    group_id: str = Field(
        description="Group 标识符",
    )
    fusion_id: str = Field(
        description="Fusion 操作标识符",
    )
    question: str = Field(
        description="原始问题",
    )
    driver_bot_id: Optional[str] = Field(
        default=None,
        description="Driver bot 标识符",
    )
    perspectives: list[Perspective] = Field(
        default_factory=list,
        description="收集到的视角列表",
    )
    recommendation: Optional[Recommendation] = Field(
        default=None,
        description="融合建议（G1/G2 单一高层建议）",
    )
    partial_success: bool = Field(
        description="是否部分成功",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="警告信息",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="错误信息",
    )
    timing: FusionTiming = Field(
        description="计时信息",
    )
    # G2/G5 扩展字段 - fusion_mode
    fusion_mode: Literal["agent", "conflict_alignment", "expert_diagnosis", "bot_profile_fuse"] = Field(
        default="agent",
        description="融合模式：agent（G1）、conflict_alignment（G2）、expert_diagnosis（G5）或 bot_profile_fuse（G9）",
    )
    # G2 扩展字段
    conflicts: list[Any] = Field(
        default_factory=list,
        description="识别的冲突列表（G2），类型为 FusionConflict",
    )
    alignment_points: list[Any] = Field(
        default_factory=list,
        description="达成的对齐点列表（G2），类型为 FusionAlignmentPoint",
    )
    key_insights: list[str] = Field(
        default_factory=list,
        description="关键洞察列表（G2）",
    )
    # G5 扩展字段
    risk_assessment: Optional[Any] = Field(
        default=None,
        description="风险评估（G5），类型为 RiskAssessment",
    )
    critical_issues: list[Any] = Field(
        default_factory=list,
        description="关键问题列表（G5），类型为 CriticalIssue",
    )
    recommendations: list[Any] = Field(
        default_factory=list,
        description="专家建议列表（G5，多个行动项），类型为 ExpertRecommendation",
    )
    go_live_conditions: list[str] = Field(
        default_factory=list,
        description="上线条件列表（G5）",
    )
    summary: Optional[str] = Field(
        default=None,
        description="诊断摘要（G5）",
    )
    # G5 V2 扩展字段 - 结构化风险评估
    structured_risk: Optional[Any] = Field(
        default=None,
        description="结构化风险评估（G5 V2），类型为 StructuredRiskAssessment",
    )
    # G2 V2 扩展字段 - 结构化冲突分析
    structured_conflict_analysis: Optional[Any] = Field(
        default=None,
        description="结构化冲突分析（G2 V2），类型为 StructuredConflictAnalysis",
    )
    # G2 结论字段 - 冲突分析综合结论
    conclusion: Optional[Any] = Field(
        default=None,
        description="冲突结论（G2），类型为 ConflictConclusion",
    )
    # G2 分析来源 - 三层Fallback架构来源标识
    analysis_source: Optional[str] = Field(
        default=None,
        description="G2分析来源（llm/v2/legacy），标识三层Fallback使用的是哪一层",
    )
    # 扩展结果字段 - 存放各模式的扩展处理信息
    extend_result: Optional[dict[str, Any]] = Field(
        default=None,
        description="扩展结果，G9模式包含fused_profile和group_conversation",
    )
    # Phase D2: Metadata field for diagnostics and profiling
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata 包含诊断信息、profile 加载统计、执行路径追踪等",
    )

    @property
    def is_success(self) -> bool:
        """
        判断是否成功

        只要 partial_success 为 True 或没有 errors，就认为成功。
        """
        return self.partial_success or len(self.errors) == 0


class FusionError(BaseModel):
    """
    融合错误

    用于错误响应的错误对象。
    """

    model_config = {"extra": "forbid"}

    code: str = Field(
        description="错误码",
    )
    message: str = Field(
        description="错误消息",
    )
    details: list[str] = Field(
        default_factory=list,
        description="错误详情",
    )


__all__ = [
    "FusionResult",
    "Perspective",
    "Recommendation",
    "FusionTiming",
    "FusionError",
]