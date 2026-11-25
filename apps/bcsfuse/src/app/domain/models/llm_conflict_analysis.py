"""
LLM冲突分析结果模型

G2 Conflict Alignment Layer - Phase 1

定义LLM冲突分析的输出数据结构。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class StanceAnalysis(BaseModel):
    """
    LLM分析的立场结果

    描述一个参与方在冲突场景中的立场。

    Attributes:
        participant_id: 参与方ID
        stance: 立场倾向（支持/反对/有条件支持/中立）
        core_demand: 核心诉求
        main_concerns: 主要顾虑列表
        flexibility: 灵活程度（不可妥协/可协商/开放态度）
        rationale: 立场理由
    """

    model_config = {"extra": "forbid"}

    participant_id: str = Field(
        description="参与方ID",
    )
    stance: Literal["支持", "反对", "有条件支持", "中立"] = Field(
        description="立场倾向",
    )
    core_demand: str = Field(
        description="核心诉求",
    )
    main_concerns: list[str] = Field(
        default_factory=list,
        description="主要顾虑列表",
    )
    flexibility: Literal["不可妥协", "可协商", "开放态度"] = Field(
        description="灵活程度",
    )
    rationale: str = Field(
        default="",
        description="立场理由",
    )


class LLMConflict(BaseModel):
    """
    LLM识别的冲突

    描述两个或多个参与方之间的冲突关系。

    Attributes:
        parties: 冲突涉及的参与方列表
        conflict_type: 冲突类型（立场对立/诉求冲突/关注点分歧/风险偏好差异）
        issue: 冲突核心问题
        severity: 冲突严重程度（critical/high/medium/low）
        analysis: 冲突分析说明
    """

    model_config = {"extra": "forbid"}

    parties: list[str] = Field(
        description="冲突涉及的参与方列表",
        min_length=2,
    )
    conflict_type: Literal["立场对立", "诉求冲突", "关注点分歧", "风险偏好差异"] = Field(
        description="冲突类型",
    )
    issue: str = Field(
        description="冲突核心问题",
    )
    severity: Literal["critical", "high", "medium", "low"] = Field(
        description="冲突严重程度",
    )
    analysis: str = Field(
        default="",
        description="冲突分析说明",
    )


class LLMAlignmentPoint(BaseModel):
    """
    LLM识别的对齐点

    描述参与方之间的共识基础。

    Attributes:
        participants: 对齐点涉及的参与方列表
        point: 对齐点描述
        significance: 对齐点的意义
    """

    model_config = {"extra": "forbid"}

    participants: list[str] = Field(
        description="对齐点涉及的参与方列表",
        min_length=2,
    )
    point: str = Field(
        description="对齐点描述",
    )
    significance: str = Field(
        default="",
        description="对齐点的意义",
    )


class LLMConclusion(BaseModel):
    """
    LLM综合结论

    描述冲突分析的综合研判结果。

    Attributes:
        overall_severity: 整体冲突严重程度
        go_no_go: 推进建议（go/conditional_go/need_discussion/no_go）
        resolution_strategy: 解决策略
        conditions: 推进条件列表
        priority_actions: 优先行动列表
        reasoning: 研判理由
    """

    model_config = {"extra": "forbid"}

    overall_severity: Literal["critical", "high", "medium", "low"] = Field(
        description="整体冲突严重程度",
    )
    go_no_go: Literal["go", "conditional_go", "need_discussion", "no_go"] = Field(
        description="推进建议",
    )
    resolution_strategy: str = Field(
        default="",
        description="解决策略",
    )
    conditions: list[str] = Field(
        default_factory=list,
        description="推进条件列表",
    )
    priority_actions: list[str] = Field(
        default_factory=list,
        description="优先行动列表",
    )
    reasoning: str = Field(
        default="",
        description="研判理由",
    )


class LLMConflictAnalysis(BaseModel):
    """
    LLM冲突分析完整结果

    包含所有分析输出的完整数据结构。

    Attributes:
        stance_analysis: 各方立场分析列表
        conflicts: 检测到的冲突列表
        alignment_points: 识别的对齐点列表
        conclusion: 综合结论
        model_used: 使用的LLM模型名称
        latency_ms: LLM调用耗时（毫秒）
    """

    model_config = {"extra": "forbid"}

    stance_analysis: list[StanceAnalysis] = Field(
        default_factory=list,
        description="各方立场分析列表",
    )
    conflicts: list[LLMConflict] = Field(
        default_factory=list,
        description="检测到的冲突列表",
    )
    alignment_points: list[LLMAlignmentPoint] = Field(
        default_factory=list,
        description="识别的对齐点列表",
    )
    conclusion: Optional[LLMConclusion] = Field(
        default=None,
        description="综合结论",
    )

    # 元信息
    model_used: str = Field(
        default="",
        description="使用的LLM模型名称",
    )
    latency_ms: int = Field(
        default=0,
        ge=0,
        description="LLM调用耗时（毫秒）",
    )

    def has_conflicts(self) -> bool:
        """是否检测到冲突"""
        return len(self.conflicts) > 0

    def has_alignment(self) -> bool:
        """是否识别到对齐点"""
        return len(self.alignment_points) > 0

    def get_critical_conflicts(self) -> list[LLMConflict]:
        """获取critical级别的冲突"""
        return [c for c in self.conflicts if c.severity == "critical"]

    def get_high_conflicts(self) -> list[LLMConflict]:
        """获取high级别的冲突"""
        return [c for c in self.conflicts if c.severity == "high"]

    def get_stance_for_participant(self, participant_id: str) -> Optional[StanceAnalysis]:
        """获取指定参与方的立场分析"""
        for sa in self.stance_analysis:
            if sa.participant_id == participant_id:
                return sa
        return None


__all__ = [
    "StanceAnalysis",
    "LLMConflict",
    "LLMAlignmentPoint",
    "LLMConclusion",
    "LLMConflictAnalysis",
]