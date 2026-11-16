"""
Evidence Model - 统一证据模型

Phase D: Unified Evidence Layer

统一 G1/G2/G5 的证据模型，提供：
- 统一的证据类型枚举
- 统一的证据来源溯源
- 结构化的支持事实链

约束：
- 这是一个内部模型层，不直接暴露到API
- 所有Feature Flags默认false
- 向后兼容现有ScoringSignal/StanceSignal/RiskFactor
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class EvidenceType(str, Enum):
    """
    证据类型枚举 - 统一G1/G2/G5

    命名规范：<领域>_<具体类型>
    """
    # G1 类型 (候选推荐)
    SKILL_MATCH = "skill_match"                    # 技能匹配
    CAPABILITY_COVERAGE = "capability_coverage"    # 能力覆盖
    SEMANTIC_SIMILARITY = "semantic_similarity"    # 语义相似度
    AVAILABILITY = "availability"                  # 可用性
    DOMAIN_EXPERTISE = "domain_expertise"          # 领域专长

    # G2 类型 (冲突检测)
    STANCE = "stance"                              # 立场
    CONFLICT_INDICATOR = "conflict_indicator"      # 冲突指示
    ALIGNMENT_INDICATOR = "alignment_indicator"    # 对齐指示

    # G5 类型 (专家诊断)
    RISK_FACTOR = "risk_factor"                    # 风险因素
    SCENARIO_MATCH = "scenario_match"              # 场景匹配

    # 通用类型
    REGISTRY_STATE = "registry_state"              # 注册状态
    EXPLICIT_INPUT = "explicit_input"              # 显式输入
    CONSTRAINT_VIOLATION = "constraint_violation"  # 约束违规


class EvidenceSource(str, Enum):
    """
    证据来源枚举 - 用于溯源

    按可信度排序（高→低）：
    1. DENSE_RETRIEVAL - Embedding向量召回（Phase E主路径）
    2. LLM_INFERENCE - LLM推断
    3. SPARSE_RETRIEVAL - 关键词/BM25召回
    4. TAXONOMY_PRIOR - Taxonomy先验（降级后的辅助层）
    5. RULE_BASED - 规则计算
    """
    # 高可信来源
    DENSE_RETRIEVAL = "dense_retrieval"       # Embedding驱动召回（Phase E）
    LLM_INFERENCE = "llm_inference"          # LLM推断结果
    SPARSE_RETRIEVAL = "sparse_retrieval"    # 关键词/BM25召回

    # 辅助来源
    TAXONOMY_PRIOR = "taxonomy_prior"        # Taxonomy先验知识
    REGISTRY_STATE = "registry_state"        # Worker注册状态
    RULE_BASED = "rule_based"                # 规则计算

    # 显式来源
    EXPLICIT_INPUT = "explicit_input"        # 用户显式输入
    CONSTRAINT_CHECK = "constraint_check"    # 约束检查结果


class Evidence(BaseModel):
    """
    统一证据模型

    用于表示 G1/G2/G5 各模式下的评分、冲突、风险等证据。
    所有证据都有统一的格式，便于聚合、比较和溯源。

    设计原则：
    - 单一职责：一个Evidence只表示一个证据点
    - 溯源完整：source + provenance 提供完整溯源链
    - 可聚合：raw_value + weight 便于聚合计算
    """

    # 基础标识
    evidence_id: str = Field(description="证据唯一标识")
    evidence_type: EvidenceType = Field(description="证据类型")
    source: EvidenceSource = Field(description="证据来源")
    mode: Literal["G1", "G2", "G5"] = Field(description="所属模式")

    # 核心值
    raw_value: float = Field(ge=0.0, le=1.0, description="原始值 (0.0-1.0)")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="权重 (0.0-1.0)")
    weighted_value: float = Field(default=0.0, ge=0.0, le=1.0, description="加权值 = raw_value * weight")

    # 描述与证据链
    description: str = Field(description="证据描述")
    supporting_facts: list[str] = Field(
        default_factory=list,
        description="支持事实列表（如匹配的关键词、向量相似度分值等）"
    )

    # 溯源信息
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="溯源信息：来源组件、计算方式、输入数据摘要等"
    )

    # 置信度
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="证据置信度")

    # 关联信息
    participant_id: Optional[str] = Field(default=None, description="关联参与者ID（如有）")

    # 元数据
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    computation_time_ms: Optional[int] = Field(default=None, description="计算耗时(ms)")

    model_config = {
        "extra": "forbid",
    }

    def model_post_init(self, __context):
        """计算加权值"""
        object.__setattr__(self, 'weighted_value', self.raw_value * self.weight)

    def to_legacy_signal_dict(self) -> dict[str, Any]:
        """
        转换为Legacy信号字典格式

        用于向后兼容现有ScoringSignal等模型
        """
        return {
            "signal_type": self.evidence_type.value,
            "raw_score": self.raw_value,
            "weight": self.weight,
            "weighted_score": self.weighted_value,
            "details": {
                "source": self.source.value,
                "description": self.description,
                "supporting_facts": self.supporting_facts,
                "provenance": self.provenance,
                "confidence": self.confidence,
            },
        }


class EvidenceProvenance(BaseModel):
    """
    证据溯源信息

    记录证据的计算来源、方法、输入数据等，
    便于审计和解释。
    """

    # 来源组件
    component: str = Field(description="来源组件名称")
    method: str = Field(description="计算方法")

    # 输入数据摘要
    input_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="输入数据摘要（不存储原始敏感数据）"
    )

    # 计算参数
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="计算参数（如阈值、权重等）"
    )

    # 依赖项
    dependencies: list[str] = Field(
        default_factory=list,
        description="依赖的其他证据ID列表"
    )

    model_config = {
        "extra": "allow",  # 允许扩展字段
    }


class EvidenceSourceDistribution(BaseModel):
    """证据来源分布"""

    total_count: int = Field(default=0, description="证据总数")
    by_source: dict[str, int] = Field(
        default_factory=dict,
        description="按来源统计 {source: count}"
    )
    by_mode: dict[str, int] = Field(
        default_factory=dict,
        description="按模式统计 {mode: count}"
    )
    by_type: dict[str, int] = Field(
        default_factory=dict,
        description="按类型统计 {type: count}"
    )

    def add_evidence(self, evidence: Evidence) -> None:
        """添加证据到统计"""
        self.total_count += 1
        self.by_source[evidence.source.value] = self.by_source.get(evidence.source.value, 0) + 1
        self.by_mode[evidence.mode] = self.by_mode.get(evidence.mode, 0) + 1
        self.by_type[evidence.evidence_type.value] = self.by_type.get(evidence.evidence_type.value, 0) + 1


__all__ = [
    "EvidenceType",
    "EvidenceSource",
    "Evidence",
    "EvidenceProvenance",
    "EvidenceSourceDistribution",
]