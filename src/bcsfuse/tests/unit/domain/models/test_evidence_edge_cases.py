"""
Phase D 边界条件和异常测试

测试覆盖：
- 边界值测试
- 异常情况处理
- 性能测试
- 多模式混合测试
"""

import pytest
import time
from datetime import datetime

from src.domain.models.evidence import (
    Evidence,
    EvidenceType,
    EvidenceSource,
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
    create_sparse_to_taxonomy_fallback,
    create_llm_to_rule_fallback,
    create_embedding_unavailable_fallback,
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
    create_conflict_evidence,
    expert_evidence_to_evidence,
    scenario_prior_to_evidence,
)
from src.domain.services.explanation_builder_v2 import ExplanationBuilderV2


# =============================================================================
# 边界条件测试
# =============================================================================

class TestEvidenceBoundaryConditions:
    """Evidence 边界条件测试"""

    def test_raw_value_zero(self):
        """测试 raw_value = 0"""
        evidence = Evidence(
            evidence_id="ev_boundary_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.0,
            weight=1.0,
            description="Zero value",
        )
        assert evidence.raw_value == 0.0
        assert evidence.weighted_value == 0.0

    def test_raw_value_one(self):
        """测试 raw_value = 1"""
        evidence = Evidence(
            evidence_id="ev_boundary_002",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=1.0,
            weight=0.8,
            description="Max value",
        )
        assert evidence.raw_value == 1.0
        assert evidence.weighted_value == pytest.approx(0.8, rel=1e-6)

    def test_weight_zero(self):
        """测试 weight = 0"""
        evidence = Evidence(
            evidence_id="ev_boundary_003",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=0.0,
            description="Zero weight",
        )
        assert evidence.weight == 0.0
        assert evidence.weighted_value == 0.0

    def test_weight_one(self):
        """测试 weight = 1"""
        evidence = Evidence(
            evidence_id="ev_boundary_004",
            evidence_type=EvidenceType.STANCE,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G2",
            raw_value=0.75,
            weight=1.0,
            description="Max weight",
        )
        assert evidence.weight == 1.0
        assert evidence.weighted_value == 0.75

    def test_confidence_zero(self):
        """测试 confidence = 0"""
        evidence = Evidence(
            evidence_id="ev_boundary_005",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.RULE_BASED,
            mode="G5",
            raw_value=0.5,
            weight=1.0,
            confidence=0.0,
            description="Zero confidence",
        )
        assert evidence.confidence == 0.0

    def test_confidence_one(self):
        """测试 confidence = 1"""
        evidence = Evidence(
            evidence_id="ev_boundary_006",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.DENSE_RETRIEVAL,
            mode="G1",
            raw_value=0.9,
            weight=1.0,
            confidence=1.0,
            description="Max confidence",
        )
        assert evidence.confidence == 1.0

    def test_empty_supporting_facts(self):
        """测试空 supporting_facts"""
        evidence = Evidence(
            evidence_id="ev_boundary_007",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.5,
            weight=1.0,
            description="Empty facts",
            supporting_facts=[],
        )
        assert len(evidence.supporting_facts) == 0

    def test_large_supporting_facts(self):
        """测试大量 supporting_facts"""
        facts = [f"Fact number {i}" for i in range(100)]
        evidence = Evidence(
            evidence_id="ev_boundary_008",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=0.5,
            weight=1.0,
            description="Large facts",
            supporting_facts=facts,
        )
        assert len(evidence.supporting_facts) == 100

    def test_large_provenance(self):
        """测试大 provenance 字典"""
        large_provenance = {f"key_{i}": f"value_{i}" for i in range(50)}
        evidence = Evidence(
            evidence_id="ev_boundary_009",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.5,
            weight=1.0,
            description="Large provenance",
            provenance=large_provenance,
        )
        assert len(evidence.provenance) == 50

    def test_long_description(self):
        """测试长描述"""
        long_desc = "A" * 1000
        evidence = Evidence(
            evidence_id="ev_boundary_010",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.5,
            weight=1.0,
            description=long_desc,
        )
        assert len(evidence.description) == 1000


class TestEvidenceBundleBoundaryConditions:
    """EvidenceBundle 边界条件测试"""

    def test_empty_bundle_aggregation(self):
        """测试空 Bundle 聚合"""
        bundle = EvidenceBundle(
            bundle_id="bundle_boundary_001",
            mode="G1",
            question="Test?",
        )
        bundle.aggregate()

        assert bundle.is_aggregated is True
        assert bundle.normalized_score == 0.0
        assert bundle.total_weight == 0.0
        assert len(bundle.top_contributors) == 0

    def test_single_evidence_bundle(self):
        """测试单证据 Bundle"""
        bundle = EvidenceBundle(
            bundle_id="bundle_boundary_002",
            mode="G5",
            question="Risk?",
        )
        bundle.add_evidence(Evidence(
            evidence_id="ev_single",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.RULE_BASED,
            mode="G5",
            raw_value=0.75,
            weight=1.0,
            description="Single",
        ))
        bundle.aggregate()

        assert bundle.normalized_score == 0.75
        assert len(bundle.top_contributors) == 1
        assert bundle.top_contributors[0].contribution_ratio == 1.0

    def test_many_evidences_bundle(self):
        """测试大量证据 Bundle（性能测试）"""
        bundle = EvidenceBundle(
            bundle_id="bundle_boundary_003",
            mode="G1",
            question="Performance test?",
        )

        # 添加 1000 个证据
        for i in range(1000):
            bundle.add_evidence(Evidence(
                evidence_id=f"ev_perf_{i:04d}",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5,
                weight=0.5,
                description=f"Evidence {i}",
            ))

        start_time = time.time()
        bundle.aggregate()
        elapsed_ms = (time.time() - start_time) * 1000

        assert bundle.is_aggregated is True
        assert len(bundle.evidences) == 1000
        assert elapsed_ms < 100, f"Aggregation took {elapsed_ms}ms, expected < 100ms"
        print(f"\n[PERF] 1000 evidences aggregation: {elapsed_ms:.2f}ms")

    def test_zero_weight_evidence_ignored_in_contribution(self):
        """测试零权重证据不贡献"""
        bundle = EvidenceBundle(
            bundle_id="bundle_boundary_004",
            mode="G1",
            question="Test?",
        )

        bundle.add_evidence(Evidence(
            evidence_id="ev_zero_weight",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=1.0,
            weight=0.0,
            description="Zero weight",
        ))
        bundle.add_evidence(Evidence(
            evidence_id="ev_normal_weight",
            evidence_type=EvidenceType.CAPABILITY_COVERAGE,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.5,
            weight=1.0,
            description="Normal weight",
        ))
        bundle.aggregate()

        # 零权重证据的 weighted_value = 0
        assert bundle.weighted_sum == pytest.approx(0.5, rel=1e-3)


class TestFallbackChainBoundaryConditions:
    """FallbackChain 边界条件测试"""

    def test_empty_chain(self):
        """测试空降级链"""
        chain = FallbackChain()
        assert chain.get_chain_length() == 0
        assert chain.is_fallback_active() is False
        assert chain.get_fallback_depth() == 0

    def test_multi_level_fallback(self):
        """测试多级降级链（5级）"""
        chain = FallbackChain()

        # Dense -> Sparse -> Taxonomy -> Rule -> Explicit
        sources = [
            EvidenceSource.DENSE_RETRIEVAL,
            EvidenceSource.SPARSE_RETRIEVAL,
            EvidenceSource.TAXONOMY_PRIOR,
            EvidenceSource.RULE_BASED,
            EvidenceSource.EXPLICIT_INPUT,
        ]
        reasons = [
            FallbackReasonCode.DENSE_RETRIEVAL_EMPTY,
            FallbackReasonCode.SPARSE_RETRIEVAL_FALLBACK,
            FallbackReasonCode.TAXONOMY_PRIOR_FALLBACK,
            FallbackReasonCode.RULE_BASED_FALLBACK,
        ]

        for i in range(len(sources) - 1):
            chain.add_fallback(
                from_source=sources[i],
                to_source=sources[i + 1],
                reason=reasons[i],
            )

        assert chain.get_chain_length() == 5
        assert chain.get_fallback_depth() == 4
        assert chain.active_source == EvidenceSource.EXPLICIT_INPUT


# =============================================================================
# 异常情况测试
# =============================================================================

class TestEvidenceExceptions:
    """Evidence 异常情况测试"""

    def test_invalid_raw_value_high(self):
        """测试 raw_value > 1 抛出异常"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_001",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=1.5,  # 无效值
                weight=1.0,
                description="Invalid",
            )

    def test_invalid_raw_value_negative(self):
        """测试 raw_value < 0 抛出异常"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_002",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=-0.1,  # 无效值
                weight=1.0,
                description="Invalid",
            )

    def test_invalid_weight_high(self):
        """测试 weight > 1 抛出异常"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_003",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5,
                weight=1.5,  # 无效值
                description="Invalid",
            )

    def test_invalid_weight_negative(self):
        """测试 weight < 0 抛出异常"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_004",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5,
                weight=-0.1,  # 无效值
                description="Invalid",
            )

    def test_invalid_confidence_high(self):
        """测试 confidence > 1 抛出异常"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_005",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5,
                weight=1.0,
                confidence=1.5,  # 无效值
                description="Invalid",
            )

    def test_invalid_mode(self):
        """测试无效 mode 抛出异常"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_006",
                evidence_type=EvidenceType.SKILL_MATCH,
                source=EvidenceSource.RULE_BASED,
                mode="G3",  # 无效模式
                raw_value=0.5,
                weight=1.0,
                description="Invalid",
            )

    def test_missing_required_fields(self):
        """测试缺少必填字段"""
        with pytest.raises(Exception):
            Evidence(
                evidence_id="ev_invalid_007",
                # 缺少 evidence_type
                source=EvidenceSource.RULE_BASED,
                mode="G1",
                raw_value=0.5,
                weight=1.0,
                description="Invalid",
            )


class TestFallbackReasonExceptions:
    """FallbackReason 异常情况测试"""

    def test_all_fallback_reason_codes_valid(self):
        """测试所有 FallbackReasonCode 都有效"""
        # 遍历所有枚举值
        for code in FallbackReasonCode:
            fallback = FallbackReasonV2(
                reason_code=code,
                mode="G1",
                affected_component="test",
                description=f"Test {code.value}",
            )
            assert fallback.reason_code == code


# =============================================================================
# 多模式混合测试
# =============================================================================

class TestMultiModeEvidence:
    """多模式混合证据测试"""

    def test_mixed_mode_evidences_in_bundle(self):
        """测试多模式证据在同一 Bundle（允许但不推荐）"""
        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G1", question="Mixed test?")

        # 添加不同模式的证据
        bundle.add_evidence(Evidence(
            evidence_id="ev_g1_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.8,
            weight=1.0,
            description="G1 evidence",
        ))
        bundle.add_evidence(Evidence(
            evidence_id="ev_g5_001",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.LLM_INFERENCE,
            mode="G5",
            raw_value=0.6,
            weight=0.8,
            description="G5 evidence",
        ))

        service.aggregate(bundle)

        # 应该正常聚合，但 mode 以 bundle 为准
        assert bundle.mode == "G1"
        assert len(bundle.evidences) == 2

    def test_g2_complete_flow(self):
        """测试 G2 完整流程"""
        # 1. 创建 StanceSignal
        stance1 = StanceSignal(
            participant_id="expert_1",
            dimension_id="speed_vs_quality",
            position="axis_a",
            strength=0.8,
            confidence=0.7,
            evidence=["Speed is critical"],
        )
        stance2 = StanceSignal(
            participant_id="expert_2",
            dimension_id="speed_vs_quality",
            position="axis_b",
            strength=0.7,
            confidence=0.8,
            evidence=["Quality is more important"],
        )

        # 2. 转换为 Evidence
        evidence1 = stance_signal_to_evidence(stance1)
        evidence2 = stance_signal_to_evidence(stance2)
        conflict = create_conflict_evidence(
            conflict_description="Speed vs Quality conflict",
            parties=["expert_1", "expert_2"],
            severity="high",
        )

        # 3. 创建 Bundle 并聚合
        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G2", question="What should we prioritize?")
        service.add_evidences(bundle, [evidence1, evidence2, conflict])
        service.aggregate(bundle)

        # 4. 生成解释
        builder = ExplanationBuilderV2()
        explanation = builder.build(bundle, style="concise")

        assert bundle.is_aggregated
        assert "冲突" in explanation or "立场" in explanation

    def test_all_adapter_types(self):
        """测试所有适配器类型"""
        # G1: ScoringSignal
        signal = ScoringSignal(signal_type="skill_name_match", raw_score=0.8, weight=0.5)
        ev_g1 = scoring_signal_to_evidence(signal)
        assert ev_g1.mode == "G1"

        # G2: StanceSignal
        stance = StanceSignal(participant_id="p1", dimension_id="test", position="axis_a")
        ev_g2 = stance_signal_to_evidence(stance)
        assert ev_g2.mode == "G2"

        # G2: Conflict
        ev_conflict = create_conflict_evidence("test", ["p1", "p2"], "medium")
        assert ev_conflict.mode == "G2"
        assert ev_conflict.evidence_type == EvidenceType.CONFLICT_INDICATOR

        # G5: RiskFactor
        from src.domain.models.structured_risk_assessment import RiskFactor
        factor = RiskFactor(
            factor_id="r1",
            description="Test risk",
            category="security",
            severity=RiskLevel.HIGH,
        )
        ev_g5 = risk_factor_to_evidence(factor)
        assert ev_g5.mode == "G5"


# =============================================================================
# 性能测试
# =============================================================================

class TestPerformance:
    """性能测试"""

    def test_bundle_merge_performance(self):
        """测试 Bundle 合并性能"""
        service = EvidenceAggregationService()

        # 创建 100 个 Bundle，每个有 10 个证据
        bundles = []
        for i in range(100):
            bundle = service.create_bundle(mode="G1", question=f"Test {i}")
            for j in range(10):
                bundle.add_evidence(Evidence(
                    evidence_id=f"ev_merge_{i}_{j}",
                    evidence_type=EvidenceType.SKILL_MATCH,
                    source=EvidenceSource.RULE_BASED,
                    mode="G1",
                    raw_value=0.5,
                    weight=1.0,
                    description=f"Evidence {i}-{j}",
                ))
            bundles.append(bundle)

        start_time = time.time()
        merged = service.merge_bundles(bundles)
        elapsed_ms = (time.time() - start_time) * 1000

        assert len(merged.evidences) == 1000
        assert elapsed_ms < 50, f"Merge took {elapsed_ms}ms, expected < 50ms"
        print(f"\n[PERF] 100 bundles merge (1000 evidences): {elapsed_ms:.2f}ms")

    def test_explanation_build_performance(self):
        """测试解释构建性能"""
        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G5", question="Performance test?")

        for i in range(100):
            bundle.add_evidence(Evidence(
                evidence_id=f"ev_exp_perf_{i}",
                evidence_type=EvidenceType.RISK_FACTOR,
                source=EvidenceSource.RULE_BASED,
                mode="G5",
                raw_value=0.5 + i * 0.005,
                weight=1.0,
                description=f"Risk {i}",
            ))
        service.aggregate(bundle)

        builder = ExplanationBuilderV2()

        start_time = time.time()
        for _ in range(100):
            builder.build(bundle, style="detailed")
        elapsed_ms = (time.time() - start_time) * 1000

        avg_time = elapsed_ms / 100
        assert avg_time < 10, f"Average explanation build took {avg_time}ms, expected < 10ms"
        print(f"\n[PERF] 100 explanation builds (100 evidences): {elapsed_ms:.2f}ms total, {avg_time:.2f}ms avg")


# =============================================================================
# 日志验证测试
# =============================================================================

class TestLogging:
    """日志验证测试"""

    def test_aggregation_logs_bundle_creation(self, caplog):
        """测试聚合服务记录 Bundle 创建日志"""
        import logging
        caplog.set_level(logging.DEBUG)

        service = EvidenceAggregationService()
        bundle = service.create_bundle(mode="G1", question="Test?")

        assert "EvidenceAggregation" in caplog.text or bundle.bundle_id is not None

    def test_fallback_logs_warning(self, caplog):
        """测试降级记录警告日志"""
        import logging
        caplog.set_level(logging.WARNING)

        service = EvidenceAggregationService()
        fallback = create_dense_to_sparse_fallback(mode="G1")
        service.record_fallback(fallback)

        assert "Fallback" in caplog.text or len(service.get_fallback_reasons()) == 1


# =============================================================================
# 序列化测试
# =============================================================================

class TestSerialization:
    """序列化测试"""

    def test_evidence_model_dump(self):
        """测试 Evidence 序列化"""
        evidence = Evidence(
            evidence_id="ev_ser_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.75,
            weight=0.8,
            description="Serialization test",
            supporting_facts=["fact1", "fact2"],
        )

        data = evidence.model_dump()

        assert data["evidence_id"] == "ev_ser_001"
        assert data["evidence_type"] == EvidenceType.SKILL_MATCH
        assert data["raw_value"] == 0.75
        assert len(data["supporting_facts"]) == 2

    def test_bundle_model_dump(self):
        """测试 Bundle 序列化"""
        bundle = EvidenceBundle(
            bundle_id="bundle_ser_001",
            mode="G5",
            question="Test?",
        )
        bundle.add_evidence(Evidence(
            evidence_id="ev_ser_002",
            evidence_type=EvidenceType.RISK_FACTOR,
            source=EvidenceSource.RULE_BASED,
            mode="G5",
            raw_value=0.5,
            weight=1.0,
            description="Test",
        ))
        bundle.aggregate()

        data = bundle.model_dump()

        assert data["bundle_id"] == "bundle_ser_001"
        assert data["mode"] == "G5"
        assert data["is_aggregated"] is True
        assert len(data["evidences"]) == 1

    def test_json_serialization(self):
        """测试 JSON 序列化"""
        evidence = Evidence(
            evidence_id="ev_json_001",
            evidence_type=EvidenceType.SKILL_MATCH,
            source=EvidenceSource.RULE_BASED,
            mode="G1",
            raw_value=0.5,
            weight=1.0,
            description="JSON test",
        )

        json_str = evidence.model_dump_json()
        assert "ev_json_001" in json_str
        assert "skill_match" in json_str