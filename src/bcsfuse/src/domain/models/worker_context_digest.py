"""
Worker Context Digest

Worker Profile Retrieval & Fusion Simulation Baseline

Task-specific 上下文摘要模型。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.context_fragment import ContextFragment
from src.domain.models.skill_profile import SkillProfile


class WorkerContextDigest(BaseModel):
    """
    Task-specific 上下文摘要模型

    从完整 WorkerProfile 中裁剪出与当前任务相关的上下文摘要。

    Attributes:
        profile_key: Profile 唯一键
        mode: 检索模式
        question: 问题/任务描述
        context_summary: 上下文摘要文本
        relevant_fragments: 相关上下文片段
        relevant_skills: 相关技能
        fragment_scores: 片段评分（文件名 -> 分数）
        skill_scores: 技能评分（技能名 -> 分数）
        reasons: 选择理由
        total_fragments: 原始片段总数
        selected_fragments: 选中片段数
        total_skills: 原始技能总数
        selected_skills: 选中技能数
    """

    # 基本信息
    profile_key: str = Field(..., min_length=1, description="Profile 唯一键")
    mode: RetrievalMode = Field(..., description="检索模式")
    question: str = Field(..., min_length=1, description="问题/任务描述")

    # 裁剪结果
    context_summary: str = Field(default="", description="上下文摘要文本")
    relevant_fragments: list[ContextFragment] = Field(
        default_factory=list,
        description="相关上下文片段"
    )
    relevant_skills: list[SkillProfile] = Field(
        default_factory=list,
        description="相关技能"
    )

    # 评分信息
    fragment_scores: dict[str, float] = Field(
        default_factory=dict,
        description="片段评分（文件名 -> 分数）"
    )
    skill_scores: dict[str, float] = Field(
        default_factory=dict,
        description="技能评分（技能名 -> 分数）"
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="选择理由"
    )

    # 统计信息
    total_fragments: int = Field(default=0, ge=0, description="原始片段总数")
    selected_fragments: int = Field(default=0, ge=0, description="选中片段数")
    total_skills: int = Field(default=0, ge=0, description="原始技能总数")
    selected_skills: int = Field(default=0, ge=0, description="选中技能数")

    @property
    def fragment_selection_ratio(self) -> float:
        """
        片段选择比例

        Returns:
            选中片段数 / 原始片段总数
        """
        if self.total_fragments == 0:
            return 0.0
        return self.selected_fragments / self.total_fragments

    @property
    def skill_selection_ratio(self) -> float:
        """
        技能选择比例

        Returns:
            选中技能数 / 原始技能总数
        """
        if self.total_skills == 0:
            return 0.0
        return self.selected_skills / self.total_skills

    @property
    def has_relevant_content(self) -> bool:
        """
        是否有相关内容

        Returns:
            是否选中了片段或技能
        """
        return len(self.relevant_fragments) > 0 or len(self.relevant_skills) > 0

    model_config = {
        "extra": "forbid",
    }


__all__ = ["WorkerContextDigest"]