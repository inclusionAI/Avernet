"""
Feature Flags - 功能开关

统一管理系统的功能开关，支持环境变量配置。

使用方式：
    from src.infra.config.feature_flags import FeatureFlags

    if FeatureFlags.is_enabled("ENABLE_G5_EXPERT_DIAGNOSIS"):
        # 执行 G5 逻辑
        pass

Feature Flags 列表：
- ENABLE_REAL_LLM: Real LLM 功能（默认 True，需要配置 LLM API）
- ENABLE_REAL_EMBEDDING: Real Embedding 功能（默认 True，需要配置 Embedding API）
- ENABLE_VECTOR_AWARE_RECOMMENDATION: 向量感知推荐（默认 True）
- ENABLE_G5_EXPERT_DIAGNOSIS: G5 专家诊断模式（默认 True）
- ENABLE_REGISTRY_AWARE_FILTERING: 基于 Registry 的候选人过滤（默认 True）
- ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING: 显式参与者可用性警告（默认 True）

G1 V2 Feature Flags (Phase C)：
- ENABLE_G1_SEMANTIC_MATCH: G1 语义匹配（默认 False）
- ENABLE_G1_PROFILE_RERANK: G1 Profile 重排序 V2 框架（默认 False）
- ENABLE_G1_SCORE_BREAKDOWN_OUTPUT: G1 评分明细输出（默认 False）

Phase D: Unified Evidence Layer：
- ENABLE_UNIFIED_EVIDENCE_LAYER: 统一 Evidence 层（默认 False）
- ENABLE_EVIDENCE_BASED_EXPLANATION: 基于 Evidence 的解释（默认 False）
- ENABLE_FALLBACK_CHAIN_TRACKING: 降级链路追踪（默认 False）

Strict Mode 开关（测试用）：
- REQUIRE_REAL_LLM: 要求必须真实调用 LLM（否则测试失败）
- REQUIRE_REAL_EMBEDDING: 要求必须真实调用 Embedding（否则测试失败）

配置方式：
1. 环境变量：export ENABLE_G5_EXPERT_DIAGNOSIS=true
2. YAML 配置：在 configs/application.yaml 中设置
3. 默认值：每个 flag 有合理的默认值
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class FeatureFlagsSettings(BaseSettings):
    """
    Feature Flags 配置

    从环境变量读取功能开关配置。

    所有 flag 默认值都是 True，允许通过环境变量关闭。

    Attributes:
        enable_real_llm: Real LLM 功能
        enable_real_embedding: Real Embedding 功能
        enable_vector_aware_recommendation: 向量感知推荐
        enable_g5_expert_diagnosis: G5 专家诊断模式
        enable_registry_aware_filtering: 基于 Registry 的候选人过滤
        enable_explicit_participant_availability_warning: 显式参与者可用性警告
    """

    # Real LLM
    enable_real_llm: bool = Field(
        default=True,
        description="启用 Real LLM 功能（需要配置 LLM API）",
    )

    # Real Embedding
    enable_real_embedding: bool = Field(
        default=True,
        description="启用 Real Embedding 功能（需要配置 embedding API）",
    )

    # Vector-aware recommendation
    enable_vector_aware_recommendation: bool = Field(
        default=True,
        description="启用向量感知推荐（需要 embedding 支持）",
    )

    # G5 Expert Diagnosis
    enable_g5_expert_diagnosis: bool = Field(
        default=True,
        description="启用 G5 专家诊断模式",
    )

    # Registry-aware filtering
    enable_registry_aware_filtering: bool = Field(
        default=False,
        description="启用基于 Registry 的候选人过滤",
    )

    # Explicit participant availability warning
    enable_explicit_participant_availability_warning: bool = Field(
        default=True,
        description="启用显式参与者可用性警告",
    )

    # =====================================
    # V2 Feature Flags (默认 False)
    # =====================================

    # Taxonomy Registry
    enable_taxonomy_registry: bool = Field(
        default=False,
        description="启用 Taxonomy 配置注册表",
    )

    # G5 Structured Risk Assessment
    enable_g5_structured_risk: bool = Field(
        default=False,
        description="启用 G5 结构化风险评估",
    )

    # G5 Scenario Prior Risk
    enable_g5_scenario_prior_risk: bool = Field(
        default=False,
        description="启用 G5 场景先验风险推断",
    )

    # Score Breakdown Output
    enable_score_breakdown_output: bool = Field(
        default=False,
        description="输出评分明细",
    )

    # =====================================
    # G2 V2 Feature Flags (Phase B)
    # =====================================

    # G2 Structured Stance
    enable_g2_structured_stance: bool = Field(
        default=True,
        description="启用 G2 结构化立场识别",
    )

    # G2 Conflict Dimensions
    enable_g2_conflict_dimensions: bool = Field(
        default=True,
        description="启用 G2 冲突维度分析",
    )

    # G2 Structured Output
    enable_g2_structured_output: bool = Field(
        default=True,
        description="启用 G2 结构化输出",
    )

    # =====================================
    # G2 LLM-driven Conflict Analysis (Phase 1)
    # =====================================

    # G2 LLM Conflict Analysis (Layer 1)
    enable_g2_llm_conflict_analysis: bool = Field(
        default=True,
        description="启用 G2 LLM深度冲突分析（三层架构Layer 1）",
    )

    # G2 LLM Stance Extraction (Layer 2 enhancement)
    enable_g2_llm_stance_extraction: bool = Field(
        default=True,
        description="启用 G2 LLM立场提取（Layer 2增强）",
    )

    # G2 Fallback to V2
    enable_g2_fallback_to_v2: bool = Field(
        default=True,
        description="Layer 1 失败时 fallback 到 Layer 2 V2",
    )

    # G2 Fallback to Legacy
    enable_g2_fallback_to_legacy: bool = Field(
        default=True,
        description="Layer 2 失败时 fallback 到 Layer 3 Legacy",
    )

    # =====================================
    # G1 V2 Feature Flags (Phase C)
    # =====================================

    # G1 Semantic Match
    enable_g1_semantic_match: bool = Field(
        default=False,
        description="启用 G1 语义匹配（taxonomy expansion 驱动的语义化文本匹配）",
    )

    # G1 Profile Rerank
    enable_g1_profile_rerank: bool = Field(
        default=False,
        description="启用 G1 Profile 重排序（V2 框架）",
    )

    # G1 Score Breakdown Output
    enable_g1_score_breakdown_output: bool = Field(
        default=False,
        description="输出 G1 评分明细到 CandidateRecommendation",
    )

    # =====================================
    # Phase D: Unified Evidence Layer (默认 False)
    # =====================================

    # Unified Evidence Layer
    enable_unified_evidence_layer: bool = Field(
        default=False,
        description="启用统一 Evidence 层（内部模型，不影响 API）",
    )

    # Evidence-based Explanation
    enable_evidence_based_explanation: bool = Field(
        default=False,
        description="启用基于 Evidence 的解释生成",
    )

    # Fallback Chain Tracking
    enable_fallback_chain_tracking: bool = Field(
        default=False,
        description="启用降级链路追踪",
    )

    # =====================================
    # Phase E: Hybrid Retrieval (默认 False)
    # =====================================

    # Dense Retrieval
    enable_dense_retrieval: bool = Field(
        default=False,
        description="启用 Dense 向量召回（embedding 主召回）",
    )

    # Sparse Retrieval
    enable_sparse_retrieval: bool = Field(
        default=False,
        description="启用 Sparse 文本召回（BM25/关键词）",
    )

    # Hybrid Retrieval
    enable_hybrid_retrieval: bool = Field(
        default=False,
        description="启用 Hybrid 混合召回（需同时开启 Dense+Sparse）",
    )

    # Retrieval Score Breakdown
    enable_retrieval_score_breakdown: bool = Field(
        default=False,
        description="输出评分明细到 HybridRetrievalResult",
    )

    # Profile Embedding Index
    enable_profile_embedding_index: bool = Field(
        default=True,  # 🔧 FIX: 改为True，因为这是向量索引构建的核心开关
        description="启用 Profile Embedding 索引构建",
    )

    # =====================================
    # Phase F: Evaluation & Feedback (默认 False)
    # =====================================

    # Evaluation Loop
    enable_evaluation_loop: bool = Field(
        default=False,
        description="启用评估循环",
    )

    # Sample Collection
    enable_sample_collection: bool = Field(
        default=False,
        description="启用样本收集",
    )

    # Feedback Attribution
    enable_feedback_attribution: bool = Field(
        default=False,
        description="启用反馈归因",
    )

    # Capability Verify
    enable_capability_verify: bool = Field(
        default=False,
        description="启用能力验证（注册后异步验证 bot 声明的能力）",
    )

    model_config = {
        "env_prefix": "",  # 不使用前缀，直接使用环境变量名
        "env_file": ".env",
        "extra": "ignore",
        "case_sensitive": False,  # 不区分大小写
    }


class FeatureFlags:
    """
    Feature Flags 管理器

    提供静态方法检查功能开关状态。

    使用示例：
        if FeatureFlags.is_g5_expert_diagnosis_enabled():
            # G5 逻辑
            pass

        if FeatureFlags.is_enabled("ENABLE_VECTOR_AWARE_RECOMMENDATION"):
            # 向量推荐逻辑
            pass
    """

    _settings: Optional[FeatureFlagsSettings] = None

    @classmethod
    def _get_settings(cls) -> FeatureFlagsSettings:
        """获取配置实例（单例）"""
        if cls._settings is None:
            cls._settings = FeatureFlagsSettings()
        return cls._settings

    @classmethod
    def is_enabled(cls, flag_name: str) -> bool:
        """
        检查功能开关是否启用

        Args:
            flag_name: 开关名称（如 "ENABLE_G5_EXPERT_DIAGNOSIS"）

        Returns:
            bool: 是否启用
        """
        # 转换为小写并去除前缀
        normalized_name = flag_name.lower().replace("enable_", "")

        settings = cls._get_settings()

        # 映射到属性名
        attr_mapping = {
            "real_llm": settings.enable_real_llm,
            "real_embedding": settings.enable_real_embedding,
            "vector_aware_recommendation": settings.enable_vector_aware_recommendation,
            "g5_expert_diagnosis": settings.enable_g5_expert_diagnosis,
            "registry_aware_filtering": settings.enable_registry_aware_filtering,
            "explicit_participant_availability_warning": settings.enable_explicit_participant_availability_warning,
            # V2 Feature Flags
            "taxonomy_registry": settings.enable_taxonomy_registry,
            "g5_structured_risk": settings.enable_g5_structured_risk,
            "g5_scenario_prior_risk": settings.enable_g5_scenario_prior_risk,
            "score_breakdown_output": settings.enable_score_breakdown_output,
            # G2 V2 Feature Flags
            "g2_structured_stance": settings.enable_g2_structured_stance,
            "g2_conflict_dimensions": settings.enable_g2_conflict_dimensions,
            "g2_structured_output": settings.enable_g2_structured_output,
            # G2 LLM Conflict Analysis (Phase 1)
            "g2_llm_conflict_analysis": settings.enable_g2_llm_conflict_analysis,
            "g2_llm_stance_extraction": settings.enable_g2_llm_stance_extraction,
            "g2_fallback_to_v2": settings.enable_g2_fallback_to_v2,
            "g2_fallback_to_legacy": settings.enable_g2_fallback_to_legacy,
            # G1 V2 Feature Flags
            "g1_semantic_match": settings.enable_g1_semantic_match,
            "g1_profile_rerank": settings.enable_g1_profile_rerank,
            "g1_score_breakdown_output": settings.enable_g1_score_breakdown_output,
            # Phase D: Unified Evidence Layer
            "unified_evidence_layer": settings.enable_unified_evidence_layer,
            "evidence_based_explanation": settings.enable_evidence_based_explanation,
            "fallback_chain_tracking": settings.enable_fallback_chain_tracking,
            # Phase E: Hybrid Retrieval
            "dense_retrieval": settings.enable_dense_retrieval,
            "sparse_retrieval": settings.enable_sparse_retrieval,
            "hybrid_retrieval": settings.enable_hybrid_retrieval,
            "retrieval_score_breakdown": settings.enable_retrieval_score_breakdown,
            "profile_embedding_index": settings.enable_profile_embedding_index,
            # Phase F: Capability Verify
            "capability_verify": settings.enable_capability_verify,
        }

        return attr_mapping.get(normalized_name, True)

    @classmethod
    def is_real_llm_enabled(cls) -> bool:
        """检查 Real LLM 是否启用"""
        return cls._get_settings().enable_real_llm

    @classmethod
    def is_real_embedding_enabled(cls) -> bool:
        """检查 Real Embedding 是否启用"""
        return cls._get_settings().enable_real_embedding

    @classmethod
    def is_vector_aware_recommendation_enabled(cls) -> bool:
        """检查向量感知推荐是否启用"""
        return cls._get_settings().enable_vector_aware_recommendation

    @classmethod
    def is_g5_expert_diagnosis_enabled(cls) -> bool:
        """检查 G5 专家诊断是否启用"""
        return cls._get_settings().enable_g5_expert_diagnosis

    @classmethod
    def is_registry_aware_filtering_enabled(cls) -> bool:
        """检查基于 Registry 的过滤是否启用"""
        return cls._get_settings().enable_registry_aware_filtering

    @classmethod
    def is_explicit_participant_availability_warning_enabled(cls) -> bool:
        """检查显式参与者可用性警告是否启用"""
        return cls._get_settings().enable_explicit_participant_availability_warning

    # =====================================
    # V2 Feature Flag 检查方法
    # =====================================

    @classmethod
    def is_taxonomy_registry_enabled(cls) -> bool:
        """检查 Taxonomy Registry 是否启用"""
        return cls._get_settings().enable_taxonomy_registry

    @classmethod
    def is_g5_structured_risk_enabled(cls) -> bool:
        """检查 G5 结构化风险评估是否启用"""
        return cls._get_settings().enable_g5_structured_risk

    @classmethod
    def is_g5_scenario_prior_risk_enabled(cls) -> bool:
        """检查 G5 场景先验风险是否启用"""
        return cls._get_settings().enable_g5_scenario_prior_risk

    @classmethod
    def is_score_breakdown_output_enabled(cls) -> bool:
        """检查评分明细输出是否启用"""
        return cls._get_settings().enable_score_breakdown_output

    # =====================================
    # G2 V2 Feature Flag 检查方法
    # =====================================

    @classmethod
    def is_g2_structured_stance_enabled(cls) -> bool:
        """检查 G2 结构化立场识别是否启用"""
        return cls._get_settings().enable_g2_structured_stance

    @classmethod
    def is_g2_conflict_dimensions_enabled(cls) -> bool:
        """检查 G2 冲突维度分析是否启用"""
        return cls._get_settings().enable_g2_conflict_dimensions

    @classmethod
    def is_g2_structured_output_enabled(cls) -> bool:
        """检查 G2 结构化输出是否启用"""
        return cls._get_settings().enable_g2_structured_output

    # =====================================
    # G1 V2 Feature Flag 检查方法
    # =====================================

    @classmethod
    def is_g1_semantic_match_enabled(cls) -> bool:
        """检查 G1 语义匹配是否启用"""
        return cls._get_settings().enable_g1_semantic_match

    @classmethod
    def is_g1_profile_rerank_enabled(cls) -> bool:
        """检查 G1 Profile 重排序是否启用"""
        return cls._get_settings().enable_g1_profile_rerank

    @classmethod
    def is_g1_score_breakdown_output_enabled(cls) -> bool:
        """检查 G1 评分明细输出是否启用"""
        return cls._get_settings().enable_g1_score_breakdown_output

    # =====================================
    # Phase D: Unified Evidence Layer 检查方法
    # =====================================

    @classmethod
    def is_unified_evidence_layer_enabled(cls) -> bool:
        """检查统一 Evidence 层是否启用"""
        return cls._get_settings().enable_unified_evidence_layer

    @classmethod
    def is_evidence_based_explanation_enabled(cls) -> bool:
        """检查基于 Evidence 的解释是否启用"""
        return cls._get_settings().enable_evidence_based_explanation

    @classmethod
    def is_fallback_chain_tracking_enabled(cls) -> bool:
        """检查降级链路追踪是否启用"""
        return cls._get_settings().enable_fallback_chain_tracking

    @classmethod
    def is_profile_embedding_index_enabled(cls) -> bool:
        """检查 Profile Embedding 索引是否启用"""
        return cls._get_settings().enable_profile_embedding_index

    # =====================================
    # Phase F: Capability Verify 检查方法
    # =====================================

    @classmethod
    def is_capability_verify_enabled(cls) -> bool:
        """检查能力验证是否启用"""
        return cls._get_settings().enable_capability_verify

    # =====================================
    # Phase E: Hybrid Retrieval 检查方法
    # =====================================

    @property
    def ENABLE_DENSE_RETRIEVAL(self) -> bool:
        """检查 Dense 召回是否启用"""
        return self._get_settings().enable_dense_retrieval

    @property
    def ENABLE_SPARSE_RETRIEVAL(self) -> bool:
        """检查 Sparse 召回是否启用"""
        return self._get_settings().enable_sparse_retrieval

    @property
    def ENABLE_HYBRID_RETRIEVAL(self) -> bool:
        """检查 Hybrid 召回是否启用"""
        return self._get_settings().enable_hybrid_retrieval

    @property
    def ENABLE_RETRIEVAL_SCORE_BREAKDOWN(self) -> bool:
        """检查召回评分明细是否启用"""
        return self._get_settings().enable_retrieval_score_breakdown

    @property
    def ENABLE_PROFILE_EMBEDDING_INDEX(self) -> bool:
        """检查 Profile Embedding 索引是否启用"""
        return self._get_settings().enable_profile_embedding_index

    @classmethod
    def get_all_flags(cls) -> dict[str, bool]:
        """
        获取所有功能开关状态

        Returns:
            dict[str, bool]: 所有开关的状态
        """
        settings = cls._get_settings()
        return {
            "ENABLE_REAL_LLM": settings.enable_real_llm,
            "ENABLE_REAL_EMBEDDING": settings.enable_real_embedding,
            "ENABLE_VECTOR_AWARE_RECOMMENDATION": settings.enable_vector_aware_recommendation,
            "ENABLE_G5_EXPERT_DIAGNOSIS": settings.enable_g5_expert_diagnosis,
            "ENABLE_REGISTRY_AWARE_FILTERING": settings.enable_registry_aware_filtering,
            "ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING": settings.enable_explicit_participant_availability_warning,
            # V2 Feature Flags
            "ENABLE_TAXONOMY_REGISTRY": settings.enable_taxonomy_registry,
            "ENABLE_G5_STRUCTURED_RISK": settings.enable_g5_structured_risk,
            "ENABLE_G5_SCENARIO_PRIOR_RISK": settings.enable_g5_scenario_prior_risk,
            "ENABLE_SCORE_BREAKDOWN_OUTPUT": settings.enable_score_breakdown_output,
            # G2 V2 Feature Flags
            "ENABLE_G2_STRUCTURED_STANCE": settings.enable_g2_structured_stance,
            "ENABLE_G2_CONFLICT_DIMENSIONS": settings.enable_g2_conflict_dimensions,
            "ENABLE_G2_STRUCTURED_OUTPUT": settings.enable_g2_structured_output,
            # G2 LLM Conflict Analysis (Phase 1)
            "ENABLE_G2_LLM_CONFLICT_ANALYSIS": settings.enable_g2_llm_conflict_analysis,
            "ENABLE_G2_LLM_STANCE_EXTRACTION": settings.enable_g2_llm_stance_extraction,
            "ENABLE_G2_FALLBACK_TO_V2": settings.enable_g2_fallback_to_v2,
            "ENABLE_G2_FALLBACK_TO_LEGACY": settings.enable_g2_fallback_to_legacy,
            # G1 V2 Feature Flags
            "ENABLE_G1_SEMANTIC_MATCH": settings.enable_g1_semantic_match,
            "ENABLE_G1_PROFILE_RERANK": settings.enable_g1_profile_rerank,
            "ENABLE_G1_SCORE_BREAKDOWN_OUTPUT": settings.enable_g1_score_breakdown_output,
            # Phase D: Unified Evidence Layer
            "ENABLE_UNIFIED_EVIDENCE_LAYER": settings.enable_unified_evidence_layer,
            "ENABLE_EVIDENCE_BASED_EXPLANATION": settings.enable_evidence_based_explanation,
            "ENABLE_FALLBACK_CHAIN_TRACKING": settings.enable_fallback_chain_tracking,
            # Phase E: Hybrid Retrieval
            "ENABLE_HYBRID_RETRIEVAL": settings.enable_hybrid_retrieval,
            "ENABLE_DENSE_RETRIEVAL": settings.enable_dense_retrieval,
            "ENABLE_SPARSE_RETRIEVAL": settings.enable_sparse_retrieval,
            "ENABLE_PROFILE_EMBEDDING_INDEX": settings.enable_profile_embedding_index,
            "ENABLE_RETRIEVAL_SCORE_BREAKDOWN": settings.enable_retrieval_score_breakdown,
            # Phase F: Evaluation & Feedback
            "ENABLE_EVALUATION_LOOP": settings.enable_evaluation_loop,
            "ENABLE_SAMPLE_COLLECTION": settings.enable_sample_collection,
            "ENABLE_FEEDBACK_ATTRIBUTION": settings.enable_feedback_attribution,
            # Capability Verify
            "ENABLE_CAPABILITY_VERIFY": settings.enable_capability_verify,
        }

    @classmethod
    def reset(cls):
        """重置配置实例（用于测试）"""
        cls._settings = None


__all__ = ["FeatureFlags", "FeatureFlagsSettings"]