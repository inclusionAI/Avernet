"""
Unit Tests for Phase D: Unified Evidence Layer

测试覆盖：
- Evidence 模型
- EvidenceBundle 模型
- FallbackReasonV2 模型
- EvidenceAggregationService
- Evidence Adapters
- ExplanationBuilderV2
"""

import pytest
from datetime import datetime

from src.domain.models.evidence import (
    Evidence,
    EvidenceType,
    EvidenceSource,
    EvidenceProvenance,
    EvidenceSourceDistribution,
)
from src.domain.models.evidence_bundle import (
    EvidenceBundle,
    EvidenceContribution,
)
from src.domain.models.fallback_reason_v2 import (
    FallbackReasonCode,
    FallbackChain,
    FallbackReasonV2,
    create_dense_to_sparse_fallback,
    create_llm_to_rule_fallback,
)
from src.domain.models.scoring_signal import ScoringSignal
from src.domain.models.stance_signal import StanceSignal
from src.domain.models.structured_risk_assessment import RiskFactor
from src.domain.models.expert_risk_assessment import RiskLevel
from src.domain.services.evidence_aggregation_service import (
    AggregationConfig,
    EvidenceAggregationService,
)
from src.domain.services.adapters.evidence_adapters import (
    scoring_signal_to_evidence,
    stance_signal_to_evidence,
    risk_factor_to_evidence,
)
from src.domain.services.explanation_builder_v2 import (
    ExplanationBuilderV2,
    ExplanationStyle,
)


# =============================================================================
# Evidence Model Tests
# =============================================================================

class TestEvidenceModel:
    """Evidence 模型测试"""

    def test_create_evidence_basic(self):
        """测试创建基础 Evidence"""
        evidence = Evidence(
            evidence_id="ev_test_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=0.5,
            description="Test evidence",
        )

        assert evidence.evidence_id == "ev_test_001"
        assert evidence.evidence_type == EvidenceType.SKILL_MATCH
        assert evidence.source == EvidenceSource.RULE_BASED
        assert evidence.mode == "G1"
        assert evidence.raw_value == 0.8
        assert evidence.weight == 0.5
        assert evidence.weighted_value == 0.4  # raw_value * weight
        assert evidence.description == "Test evidence"
        assert evidence.confidence == 1.0  # 默认值
        assert evidence.participant_id is None

    def test_evidence_weighted_value_calculation(self):
        """测试加权值自动计算"""
        evidence = Evidence(
            evidence_id="ev_test_002",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=0.75,
            weight=0.8,
            description="Risk factor evidence",
        )

        assert evidence.weighted_value == pytest.approx(0.6, rel=1e-3)  # 0.75 * 0.8

    def test_evidence_with_supporting_facts(self):
        """测试带支持事实的 Evidence"""
        evidence = Evidence(
            evidence_id="ev_test_003",
            evidence_type=EvidenceType.STANCE,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G2",
            raw_value=0.9,
            weight=1.0,
            description="Stance evidence",
            supporting_facts=["fact1", "fact2", "fact3"],
            provenance={"source_component": "conflict_analyzer"},
            confidence=0.85,
            participant_id="participant_001",
        )

        assert len(evidence.supporting_facts) == 3
        assert evidence.provenance["source_component"] == "conflict_analyzer"
        assert evidence.confidence == 0.85
        assert evidence.participant_id == "participant_001"

    def test_evidence_value_constraints(self):
        """测试值约束"""
        # raw_value 必须在 [0, 1]
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_test_004",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=1.5,  # 超出范围
                weight=0.5,
                description="Invalid evidence",
            )

        # weight 必须在 [0, 1]
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_test_005",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5,
                weight=-0.1,  # 超出范围
                description="Invalid evidence",
            )

    def test_to_legacy_signal_dict(self):
        """测试转换为 Legacy 信号字典"""
        evidence = Evidence(
            evidence_id="ev_test_006",
            evidence_type=EvidenceType.CAPABILITY_COVERAGE,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.7,
            weight=0.6,
            description="Coverage evidence",
            supporting_facts=["skill1", "skill2"],
        )

        legacy = evidence.to_legacy_signal_dict()

        assert legacy["signal_type"] == "capability_coverage"
        assert legacy["raw_score"] == 0.7
        assert legacy["weight"] == 0.6
        assert legacy["weighted_score"] == 0.42
        assert "source" in legacy["details"]
        assert "supporting_facts" in legacy["details"]


class TestEvidenceSourceDistribution:
    """EvidenceSourceDistribution 测试"""

    def test_add_evidence_updates_distribution(self):
        """测试添加证据更新分布统计"""
        distribution = EvidenceSourceDistribution()

        evidence1 = Evidence(
            evidence_id="ev_dist_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.5,
            weight=1.0,
            description="Test 1",
        )

        evidence2 = Evidence(
            evidence_id="ev_dist_002",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=0.6,
            weight=1.0,
            description="Test 2",
        )

        distribution.add_evidence(evidence1)
        distribution.add_evidence(evidence2)

        assert distribution.total_count == 2
        assert distribution.by_source["rule_based"] == 1
        assert distribution.by_source["llm_inference"] == 1
        assert distribution.by_mode["G1"] == 1
        assert distribution.by_mode["G5"] == 1
        assert distribution.by_type["skill_match"] == 1
        assert distribution.by_type["risk_factor"] == 1


# =============================================================================
# EvidenceBundle Model Tests
# =============================================================================

class TestEvidenceBundle:
    """EvidenceBundle 模型测试"""

    def test_create_bundle_basic(self):
        """测试创建基础 Bundle"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_001",
            mode="G1",
            question="Test question?",
        )

        assert bundle.bundle_id == "bundle_test_001"
        assert bundle.mode == "G1"
        assert bundle.question == "Test question?"
        assert len(bundle.evidences) == 0
        assert bundle.is_aggregated is False
        assert bundle.normalized_score == 0.0

    def test_add_evidence_resets_aggregation(self):
        """测试添加证据重置聚合状态"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_002",
            mode="G1",
            question="Test?",
        )

        # 先聚合
        bundle.aggregate()
        assert bundle.is_aggregated is True

        # 添加证据应重置状态
        evidence = Evidence(
            evidence_id="ev_bundle_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=1.0,
            description="Test evidence",
        )
        bundle.add_evidence(evidence)

        assert bundle.is_aggregated is False
        assert len(bundle.evidences) == 1

    def test_aggregate_empty_bundle(self):
        """测试聚合空 Bundle"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_003",
            mode="G1",
            question="Test?",
        )

        bundle.aggregate()

        assert bundle.is_aggregated is True
        assert bundle.normalized_score == 0.0
        assert bundle.total_weight == 0.0
        assert len(bundle.top_contributors) == 0

    def test_aggregate_single_evidence(self):
        """测试聚合单个证据"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_004",
            mode="G1",
            question="Test?",
        )

        evidence = Evidence(
            evidence_id="ev_agg_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=0.5,
            description="Test evidence",
        )
        bundle.add_evidence(evidence)
        bundle.aggregate()

        assert bundle.is_aggregated is True
        assert bundle.normalized_score == 0.8  # 单个证据的 raw_value
        assert bundle.total_weight == 0.5
        assert bundle.weighted_sum == 0.4
        assert len(bundle.top_contributors) == 1
        assert bundle.top_contributors[0].contribution_ratio == 1.0

    def test_aggregate_multiple_evidences(self):
        """测试聚合多个证据"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_005",
            mode="G5",
            question="Risk assessment?",
        )

        evidences = [
            Evidence(
                evidence_id=f"ev_multi_{i}",
                evidence_type=EvidenceType.RISK_FACTOR,
                source=EvidenceSource.RULE_BASED,
                mode="G5",
                raw_value=0.5 + i * 0.1,
                weight=0.5,
                description=f"Risk factor {i}",
            )
            for i in range(3)
        ]
        bundle.add_evidences(evidences)
        bundle.aggregate()

        assert bundle.is_aggregated is True
        assert bundle.total_weight == 1.5  # 3 * 0.5
        assert len(bundle.top_contributors) == 3

        # 检查贡献排序（降序）
        assert bundle.top_contributors[0].rank == 1
        assert bundle.top_contributors[0].contribution_ratio > bundle.top_contributors[1].contribution_ratio

    def test_get_evidences_by_type(self):
        """测试按类型获取证据"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_006",
            mode="G2",
            question="Conflict analysis?",
        )

        bundle.add_evidences([
            Evidence(
                evidence_id="ev_type_1",
                evidence_type=EvidenceType.STANCE,
                source=EvidenceSource.LLM_INFERENCE,
                mode="G2",
                raw_value=0.8,
                weight=1.0,
                description="Stance 1",
            ),
            Evidence(
                evidence_id="ev_type_2",
                evidence_type=EvidenceType.STANCE,
                source=EvidenceSource.LLM_INFERENCE,
                mode="G2",
                raw_value=0.6,
                weight=1.0,
                description="Stance 2",
            ),
            Evidence(
                evidence_id="ev_type_3",
                evidence_type=EvidenceType.CONFLICT_INDICATOR,
                source=EvidenceSource.RULE_BASED,
                mode="G2",
                raw_value=0.9,
                weight=1.0,
                description="Conflict",
            ),
        ])

        stances = bundle.get_evidences_by_type(EvidenceType.STANCE)
        conflicts = bundle.get_evidences_by_type(EvidenceType.CONFLICT_INDICATOR)

        assert len(stances) == 2
        assert len(conflicts) == 1

    def test_get_top_k_contributors(self):
        """测试获取 Top-K 贡献者"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_007",
            mode="G1",
            question="Test?",
        )

        for i in range(5):
            bundle.add_evidence(Evidence(
                evidence_id=f"ev_top_{i}",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.1 * (i + 1),  # 0.1, 0.2, 0.3, 0.4, 0.5
                weight=1.0,
                description=f"Evidence {i}",
            ))

        bundle.aggregate()

        top_3 = bundle.get_top_k_contributors(3)
        assert len(top_3) == 3
        # 最高的应该是 ev_top_4 (raw_value=0.5)
        assert top_3[0].evidence_id == "ev_top_4"

    def test_to_explanation_context(self):
        """测试生成解释上下文"""
        bundle = EvidenceBundle(
            bundle_id="bundle_test_008",
            mode="G5",
            question="Risk assessment?",
            participant_ids=["expert_1", "expert_2"],
            strict_participants=True,
        )

        bundle.add_evidence(Evidence(
            evidence_id="ev_ctx_1",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=0.8,
            weight=1.0,
            description="Risk factor",
        ))
        bundle.aggregate()

        context = bundle.to_explanation_context()

        assert context["question"] == "Risk assessment?"
        assert context["mode"] == "G5"
        assert context["score"] == 0.8
        assert context["participant_count"] == 2
        assert context["strict_mode"] is True
        assert "top_factors" in context


# =============================================================================
# FallbackReasonV2 Model Tests
# =============================================================================

class TestFallbackReasonV2:
    """FallbackReasonV2 模型测试"""

    def test_create_fallback_reason(self):
        """测试创建降级原因"""
        fallback = FallbackReasonV2(
            reason_code=FallbackReasonCode.DENSE_RETRIEVAL_EMPTY,
            mode="G1",
            affected_component="evidence_retrieval",
            description="Dense retrieval returned empty results",
        )

        assert fallback.reason_code == FallbackReasonCode.DENSE_RETRIEVAL_EMPTY
        assert fallback.mode == "G1"
        assert fallback.affected_component == "evidence_retrieval"
        assert fallback.impact_level == "medium"  # 默认值
        assert fallback.degrade_quality is True  # 默认值

    def test_fallback_chain(self):
        """测试降级链路"""
        chain = FallbackChain()

        chain.add_fallback(
            from_source=EvidenceSource.DENSE_RETRIEVAL,
            to_source=EvidenceSource.SPARSE_RETRIEVAL,
            reason=FallbackReasonCode.DENSE_RETRIEVAL_EMPTY,
        )

        assert chain.get_chain_length() == 2
        assert chain.is_fallback_active() is True
        assert chain.get_fallback_depth() == 1
        assert chain.active_source == EvidenceSource.SPARSE_RETRIEVAL

    def test_fallback_chain_multiple_fallbacks(self):
        """测试多级降级链路"""
        chain = FallbackChain()

        # Dense -> Sparse -> Taxonomy
        chain.add_fallback(
            from_source=EvidenceSource.DENSE_RETRIEVAL,
            to_source=EvidenceSource.SPARSE_RETRIEVAL,
            reason=FallbackReasonCode.DENSE_RETRIEVAL_EMPTY,
        )
        chain.add_fallback(
            from_source=EvidenceSource.SPARSE_RETRIEVAL,
            to_source=EvidenceSource.TAXONOMY_PRIOR,
            reason=FallbackReasonCode.SPARSE_RETRIEVAL_FALLBACK,
        )

        assert chain.get_chain_length() == 3
        assert chain.get_fallback_depth() == 2
        assert chain.active_source == EvidenceSource.TAXONOMY_PRIOR

    def test_factory_functions(self):
        """测试工厂函数"""
        # Dense to Sparse
        fallback1 = create_dense_to_sparse_fallback(
            mode="G1",
            request_id="req_001",
            latency_ms=100,
        )
        assert fallback1.reason_code == FallbackReasonCode.DENSE_RETRIEVAL_EMPTY
        assert fallback1.from_source == EvidenceSource.DENSE_RETRIEVAL
        assert fallback1.to_source == EvidenceSource.SPARSE_RETRIEVAL

        # LLM to Rule
        fallback2 = create_llm_to_rule_fallback(
            mode="G5",
            request_id="req_002",
        )
        assert fallback2.reason_code == FallbackReasonCode.LLM_TO_RULE_FALLBACK
        assert fallback2.from_source == EvidenceSource.LLM_INFERENCE
        assert fallback2.to_source == EvidenceSource.RULE_BASED

    def test_to_log_dict(self):
        """测试转换为日志字典"""
        fallback = FallbackReasonV2(
            reason_code=FallbackReasonCode.EMBEDDING_SERVICE_UNAVAILABLE,
            mode="G1",
            affected_component="embedding_service",
            description="Embedding service timeout",
            request_id="req_003",
            latency_ms=5000,
        )

        log_dict = fallback.to_log_dict()

        assert log_dict["reason_code"] == "embedding_service_unavailable"
        assert log_dict["mode"] == "G1"
        assert log_dict["affected_component"] == "embedding_service"
        assert log_dict["request_id"] == "req_003"
        assert log_dict["latency_ms"] == 5000


# =============================================================================
# Evidence Aggregation Service Tests
# =============================================================================

class TestEvidenceAggregationService:
    """EvidenceAggregationService 测试"""

    def test_create_bundle(self):
        """测试创建证据包"""
        service = EvidenceAggregationService()

        bundle = service.create_bundle(
            mode="G5",
            question="Test question?",
            request_id="req_001",
            participant_ids=["p1", "p2"],
            strict_participants=True,
        )

        assert bundle.mode == "G5"
        assert bundle.question == "Test question?"
        assert bundle.request_id == "req_001"
        assert len(bundle.participant_ids) == 2
        assert bundle.strict_participants is True

    def test_add_evidence_with_source_weight(self):
        """测试添加证据时应用来源权重"""
        config = AggregationConfig(
            source_weights={
                EvidenceSource.RULE_BASED: 0.5,  # 降低规则来源权重
            }
        )
        service = EvidenceAggregationService(config=config)

        bundle = service.create_bundle(mode="G1", question="Test?")

        evidence = Evidence(
            evidence_id="ev_weight_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=1.0,
            description="Test",
        )

        service.add_evidence(bundle, evidence)

        # 检查权重调整
        assert len(bundle.evidences) == 1
        adjusted = bundle.evidences[0]
        assert adjusted.weight == 0.5  # 1.0 * 0.5
        assert adjusted.provenance.get("original_weight") == 1.0

    def test_aggregate_with_min_confidence(self):
        """测试最小置信度过滤"""
        config = AggregationConfig(min_confidence=0.5)
        service = EvidenceAggregationService(config=config)

        bundle = service.create_bundle(mode="G1", question="Test?")

        service.add_evidences(bundle, [
            Evidence(
                evidence_id="ev_conf_1",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.8,
                weight=1.0,
                description="High confidence",
                confidence=0.9,
            ),
            Evidence(
                evidence_id="ev_conf_2",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.6,
                weight=1.0,
                description="Low confidence",
                confidence=0.3,
            ),
        ])

        service.aggregate(bundle)

        # 只有高置信度证据应被聚合
        # Evidence 1: raw_value=0.8, weight=1.0 * source_weight(RULE_BASED=0.6) = 0.6
        # weighted_value = 0.8 * 0.6 = 0.48
        assert bundle.weighted_sum == pytest.approx(0.48, rel=1e-3)

    def test_merge_bundles(self):
        """测试合并证据包"""
        service = EvidenceAggregationService()

        bundle1 = service.create_bundle(mode="G1", question="Test?")
        bundle1.add_evidence(Evidence(
            evidence_id="ev_merge_1",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.7,
            weight=1.0,
            description="Evidence 1",
        ))

        bundle2 = service.create_bundle(mode="G1", question="Test?")
        bundle2.add_evidence(Evidence(
            evidence_id="ev_merge_2",
            evidence_type=EvidenceType.CAPABILITY_COVERAGE,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=1.0,
            description="Evidence 2",
        ))

        merged = service.merge_bundles([bundle1, bundle2])

        assert len(merged.evidences) == 2
        assert merged.is_aggregated is True


# =============================================================================
# Evidence Adapters Tests
# =============================================================================

class TestEvidenceAdapters:
    """Evidence Adapters 测试"""

    def test_scoring_signal_to_evidence(self):
        """测试 G1 ScoringSignal 转换"""
        signal = ScoringSignal(
            signal_type="skill_name_match",
            raw_score=0.85,
            weight=0.6,
            details={"matched_skill": "Python"},
        )

        evidence = scoring_signal_to_evidence(
            signal,
            participant_id="worker_001",
        )

        assert evidence.evidence_type == EvidenceType.SKILL_MATCH
        assert evidence.mode == "G1"
        assert evidence.raw_value == 0.85
        assert evidence.weight == 0.6
        assert evidence.participant_id == "worker_001"
        assert "matched_skill" in str(evidence.supporting_facts)

    def test_stance_signal_to_evidence(self):
        """测试 G2 StanceSignal 转换"""
        stance = StanceSignal(
            participant_id="expert_001",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.8,
            confidence=0.7,
            evidence=["We need fast delivery"],
            rationale="Time to market is critical",
        )

        evidence = stance_signal_to_evidence(stance)

        assert evidence.evidence_type == EvidenceType.STANCE
        assert evidence.mode == "G2"
        assert evidence.raw_value == 0.8
        assert evidence.weight == 0.7  # confidence 作为权重
        assert evidence.participant_id == "expert_001"
        assert len(evidence.supporting_facts) > 0

    def test_risk_factor_to_evidence(self):
        """测试 G5 RiskFactor 转换"""
        factor = RiskFactor(
            factor_id="risk_001",
            description="Data security risk",
            category="security",
            severity=RiskLevel.HIGH,
            likelihood="high",
            impact="high",
            evidence=["No encryption", "Weak authentication"],
        )

        evidence = risk_factor_to_evidence(factor)

        assert evidence.evidence_type == EvidenceType.RISK_FACTOR
        assert evidence.mode == "G5"
        assert evidence.raw_value > 0.5  # HIGH severity should have high value
        assert "Data security risk" in evidence.description
        assert len(evidence.supporting_facts) > 0


# =============================================================================
# ExplanationBuilderV2 Tests
# =============================================================================

class TestExplanationBuilderV2:
    """ExplanationBuilderV2 测试"""

    def test_build_concise_g1(self):
        """测试 G1 简洁版解释"""
        builder = ExplanationBuilderV2()

        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G1", question="Best candidate?")

        bundle.add_evidences([
            Evidence(
                evidence_id="ev_exp_1",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.9,
                weight=1.0,
                description="High skill match",
            ),
            Evidence(
                evidence_id="ev_exp_2",
                evidence_type=EvidenceType.CAPABILITY_COVERAGE,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.7,
                weight=0.8,
                description="Good coverage",
            ),
        ])
        service.aggregate(bundle)

        explanation = builder.build(bundle, style="concise")

        assert "推荐结果基于" in explanation
        assert "综合得分" in explanation

    def test_build_detailed(self):
        """测试详细版解释"""
        builder = ExplanationBuilderV2()

        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G5", question="Risk assessment?")

        bundle.add_evidence(Evidence(
            evidence_id="ev_detail_1",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=0.75,
            weight=0.9,
            description="Security risk",
            supporting_facts=["No encryption", "Weak auth"],
        ))
        service.aggregate(bundle)

        explanation = builder.build(bundle, style="detailed")

        assert "G5 证据解释" in explanation
        assert "聚合摘要" in explanation
        assert "主要贡献因素" in explanation

    def test_build_user_friendly_g5(self):
        """测试 G5 用户友好版解释"""
        builder = ExplanationBuilderV2()

        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G5", question="Go live?")

        bundle.add_evidence(Evidence(
            evidence_id="ev_uf_1",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.RULE_BASED,
            mode="G5",
            raw_value=0.8,
            weight=1.0,
            description="High risk factor",
        ))
        service.aggregate(bundle)

        explanation = builder.build(bundle, style="user_friendly")

        assert "风险" in explanation

    def test_build_factors_list(self):
        """测试构建因素列表"""
        builder = ExplanationBuilderV2()

        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G1", question="Test?")

        for i in range(3):
            bundle.add_evidence(Evidence(
                evidence_id=f"ev_list_{i}",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5 + i * 0.1,
                weight=1.0,
                description=f"Evidence {i}",
            ))
        service.aggregate(bundle)

        factors = builder.build_factors_list(bundle, max_factors=3)

        assert len(factors) == 3
        assert "type" in factors[0]
        assert "contribution" in factors[0]
        assert "type_label" in factors[0]

    def test_build_source_attribution(self):
        """测试来源归因"""
        builder = ExplanationBuilderV2()

        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G1", question="Test?")

        bundle.add_evidences([
            Evidence(
                evidence_id="ev_attr_1",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.8,
                weight=1.0,
                description="Rule-based",
            ),
            Evidence(
                evidence_id="ev_attr_2",
                evidence_type=EvidenceType.SEMANTIC_SIMILARITY,
                source=EvidenceSource.LLM_INFERENCE,
                mode="G1",
                raw_value=0.6,
                weight=1.0,
                description="LLM-based",
            ),
        ])
        service.aggregate(bundle)

        attribution = builder.build_source_attribution(bundle)

        assert attribution["total"] > 0
        assert "by_source" in attribution
        assert "rule_based" in attribution["by_source"]
        assert "llm_inference" in attribution["by_source"]


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhaseDIntegration:
    """Phase D 集成测试"""

    def test_full_flow_g1(self):
        """测试 G1 完整流程"""
        # 1. 创建信号
        signal = ScoringSignal(
            signal_type="skill_name_match",
            raw_score=0.9,
            weight=0.8,
            details={"skill": "Python"},
        )

        # 2. 转换为 Evidence
        evidence = scoring_signal_to_evidence(signal, participant_id="worker_001")

        # 3. 创建 Bundle 并聚合
        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G1", question="Best candidate?")
        service.add_evidence(bundle, evidence)
        service.aggregate(bundle)

        # 4. 生成解释
        builder = ExplanationBuilderV2()
        explanation = builder.build(bundle, style="concise")

        assert bundle.is_aggregated
        assert bundle.normalized_score == pytest.approx(0.9, rel=1e-3)
        assert "推荐" in explanation or "得分" in explanation

    def test_full_flow_g5(self):
        """测试 G5 完整流程"""
        # 1. 创建风险因素
        factor = RiskFactor(
            factor_id="security_001",
            description="Security vulnerability",
            category="security",
            severity=RiskLevel.HIGH,
            likelihood="high",
            impact="high",
        )

        # 2. 转换为 Evidence
        evidence = risk_factor_to_evidence(factor)

        # 3. 聚合
        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G5", question="Is it safe to deploy?")
        service.add_evidence(bundle, evidence)
        service.aggregate(bundle)

        # 4. 生成解释
        builder = ExplanationBuilderV2()
        summary = builder.build_summary(bundle)
        detailed = builder.build(bundle, style="detailed", include_source=True)

        assert bundle.is_aggregated
        assert "风险" in summary
        assert "G5" in detailed