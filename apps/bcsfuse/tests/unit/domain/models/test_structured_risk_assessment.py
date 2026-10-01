"""
Structured Risk Assessment Model Tests

Phase A: G5 Risk Engine V2 结构化风险评估模型测试
"""

from __future__ import annotations

import pytest

from src.domain.models.structured_risk_assessment import (
    RiskFactor,
    BlockingCondition,
    ExpertEvidence,
    ScenarioPriorRisk,
    StructuredRiskAssessment,
)
from src.domain.models.expert_risk_assessment import RiskLevel


# =============================================================================
# Test: RiskFactor Model
# =============================================================================

class TestRiskFactor:
    """RiskFactor 模型测试"""

    def test_create_risk_factor_basic(self):
        """测试基本风险因素创建"""
        factor = RiskFactor(
            factor_id="rf_001",
            description="SQL注入漏洞",
            severity=RiskLevel.HIGH,
        )

        assert factor.factor_id == "rf_001"
        assert factor.description == "SQL注入漏洞"
        assert factor.severity == RiskLevel.HIGH
        assert factor.category == "general"
        assert factor.likelihood == "medium"
        assert factor.impact == "medium"
        assert factor.evidence == []
        assert factor.expert_sources == []

    def test_create_risk_factor_full(self):
        """测试完整风险因素创建"""
        factor = RiskFactor(
            factor_id="rf_002",
            description="数据泄露风险",
            category="security",
            severity=RiskLevel.CRITICAL,
            likelihood="high",
            impact="high",
            evidence=["存在未过滤的用户输入", "缺少参数化查询"],
            expert_sources=["security_expert_001"],
        )

        assert factor.factor_id == "rf_002"
        assert factor.category == "security"
        assert factor.severity == RiskLevel.CRITICAL
        assert factor.likelihood == "high"
        assert factor.impact == "high"
        assert len(factor.evidence) == 2
        assert len(factor.expert_sources) == 1

    def test_severity_values(self):
        """测试所有严重程度值"""
        valid_severities = [
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ]

        for severity in valid_severities:
            factor = RiskFactor(
                factor_id="test",
                description="test",
                severity=severity,
            )
            assert factor.severity == severity

    def test_likelihood_values(self):
        """测试发生可能性值"""
        valid_likelihoods = ["high", "medium", "low"]

        for likelihood in valid_likelihoods:
            factor = RiskFactor(
                factor_id="test",
                description="test",
                severity=RiskLevel.MEDIUM,
                likelihood=likelihood,
            )
            assert factor.likelihood == likelihood

    def test_impact_values(self):
        """测试影响程度值"""
        valid_impacts = ["high", "medium", "low"]

        for impact in valid_impacts:
            factor = RiskFactor(
                factor_id="test",
                description="test",
                severity=RiskLevel.MEDIUM,
                impact=impact,
            )
            assert factor.impact == impact


# =============================================================================
# Test: BlockingCondition Model
# =============================================================================

class TestBlockingCondition:
    """BlockingCondition 模型测试"""

    def test_create_blocking_condition_basic(self):
        """测试基本阻塞条件创建"""
        condition = BlockingCondition(
            condition_id="bc_001",
            description="存在高危安全漏洞",
            blocking_reason="安全漏洞必须修复后才能上线",
        )

        assert condition.condition_id == "bc_001"
        assert condition.description == "存在高危安全漏洞"
        assert condition.blocking_reason == "安全漏洞必须修复后才能上线"
        assert condition.required_actions == []

    def test_create_blocking_condition_full(self):
        """测试完整阻塞条件创建"""
        condition = BlockingCondition(
            condition_id="bc_002",
            description="缺少安全审计",
            blocking_reason="必须通过安全审计",
            required_actions=["完成安全审计", "修复审计发现的问题", "重新提交审核"],
        )

        assert condition.condition_id == "bc_002"
        assert len(condition.required_actions) == 3
        assert "完成安全审计" in condition.required_actions


# =============================================================================
# Test: ExpertEvidence Model
# =============================================================================

class TestExpertEvidence:
    """ExpertEvidence 模型测试"""

    def test_create_expert_evidence_basic(self):
        """测试基本专家证据创建"""
        evidence = ExpertEvidence(
            expert_id="expert_001",
            expert_domain="security",
            evidence_text="存在SQL注入漏洞，需要立即修复",
        )

        assert evidence.expert_id == "expert_001"
        assert evidence.expert_domain == "security"
        assert evidence.evidence_text == "存在SQL注入漏洞，需要立即修复"
        assert evidence.evidence_type == "opinion"
        assert evidence.confidence == 0.5

    def test_create_expert_evidence_full(self):
        """测试完整专家证据创建"""
        evidence = ExpertEvidence(
            expert_id="expert_002",
            expert_domain="database",
            evidence_text="查询性能可能导致超时",
            evidence_type="concern",
            confidence=0.8,
        )

        assert evidence.expert_domain == "database"
        assert evidence.evidence_type == "concern"
        assert evidence.confidence == 0.8

    def test_evidence_type_values(self):
        """测试证据类型值"""
        valid_types = ["fact", "opinion", "concern", "recommendation"]

        for evidence_type in valid_types:
            evidence = ExpertEvidence(
                expert_id="test",
                expert_domain="test",
                evidence_text="test",
                evidence_type=evidence_type,
            )
            assert evidence.evidence_type == evidence_type

    def test_confidence_range(self):
        """测试置信度范围"""
        # 正常范围
        evidence = ExpertEvidence(
            expert_id="test",
            expert_domain="test",
            evidence_text="test",
            confidence=0.7,
        )
        assert evidence.confidence == 0.7

        # 边界值
        evidence_min = ExpertEvidence(
            expert_id="test",
            expert_domain="test",
            evidence_text="test",
            confidence=0.0,
        )
        assert evidence_min.confidence == 0.0

        evidence_max = ExpertEvidence(
            expert_id="test",
            expert_domain="test",
            evidence_text="test",
            confidence=1.0,
        )
        assert evidence_max.confidence == 1.0

        # 超出范围应该抛出异常
        with pytest.raises(Exception):
            ExpertEvidence(
                expert_id="test",
                expert_domain="test",
                evidence_text="test",
                confidence=1.5,
            )


# =============================================================================
# Test: ScenarioPriorRisk Model
# =============================================================================

class TestScenarioPriorRisk:
    """ScenarioPriorRisk 模型测试"""

    def test_create_scenario_prior_risk_basic(self):
        """测试基本场景先验风险创建"""
        risk = ScenarioPriorRisk(
            scenario_type="data_leakage",
            baseline_risk=RiskLevel.HIGH,
        )

        assert risk.scenario_type == "data_leakage"
        assert risk.baseline_risk == RiskLevel.HIGH
        assert risk.matched_keywords == []
        assert risk.confidence == 0.5

    def test_create_scenario_prior_risk_full(self):
        """测试完整场景先验风险创建"""
        risk = ScenarioPriorRisk(
            scenario_type="system_migration",
            matched_keywords=["架构升级", "数据库迁移", "技术栈升级"],
            baseline_risk=RiskLevel.HIGH,
            confidence=0.85,
        )

        assert risk.scenario_type == "system_migration"
        assert len(risk.matched_keywords) == 3
        assert risk.baseline_risk == RiskLevel.HIGH
        assert risk.confidence == 0.85

    def test_baseline_risk_values(self):
        """测试基线风险值"""
        valid_risks = [
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ]

        for baseline_risk in valid_risks:
            risk = ScenarioPriorRisk(
                scenario_type="test",
                baseline_risk=baseline_risk,
            )
            assert risk.baseline_risk == baseline_risk


# =============================================================================
# Test: StructuredRiskAssessment Model
# =============================================================================

class TestStructuredRiskAssessment:
    """StructuredRiskAssessment 模型测试"""

    def test_create_basic_assessment(self):
        """测试基本风险评估创建"""
        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.MEDIUM,
        )

        assert assessment.risk_level == RiskLevel.MEDIUM
        assert assessment.baseline_risk is None
        assert assessment.risk_factors == []
        assert assessment.blocking_conditions == []
        assert assessment.supporting_evidence == []
        assert assessment.decision_rationale == ""
        assert assessment.scenario_prior_risk is None

    def test_create_full_assessment(self):
        """测试完整风险评估创建"""
        risk_factor = RiskFactor(
            factor_id="rf_001",
            description="性能瓶颈风险",
            category="performance",
            severity=RiskLevel.MEDIUM,
        )

        blocking = BlockingCondition(
            condition_id="bc_001",
            description="未通过压力测试",
            blocking_reason="必须通过压力测试",
        )

        evidence = ExpertEvidence(
            expert_id="dba_001",
            expert_domain="database",
            evidence_text="缺少必要的索引",
            evidence_type="concern",
        )

        scenario_risk = ScenarioPriorRisk(
            scenario_type="large_promotion",
            matched_keywords=["大促", "营销活动"],
            baseline_risk=RiskLevel.HIGH,
        )

        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.HIGH,
            baseline_risk=RiskLevel.HIGH,
            risk_factors=[risk_factor],
            blocking_conditions=[blocking],
            supporting_evidence=[evidence],
            decision_rationale="存在性能和压力测试相关问题",
            scenario_prior_risk=scenario_risk,
        )

        assert assessment.risk_level == RiskLevel.HIGH
        assert assessment.baseline_risk == RiskLevel.HIGH
        assert len(assessment.risk_factors) == 1
        assert len(assessment.blocking_conditions) == 1
        assert len(assessment.supporting_evidence) == 1
        assert assessment.decision_rationale == "存在性能和压力测试相关问题"
        assert assessment.scenario_prior_risk is not None

    def test_has_blocking_conditions_true(self):
        """测试有阻塞条件的判断"""
        blocking = BlockingCondition(
            condition_id="bc_001",
            description="安全漏洞",
            blocking_reason="必须修复",
        )

        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.HIGH,
            blocking_conditions=[blocking],
        )

        assert assessment.has_blocking_conditions() is True

    def test_has_blocking_conditions_false(self):
        """测试无阻塞条件的判断"""
        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.LOW,
        )

        assert assessment.has_blocking_conditions() is False

    def test_get_high_severity_factors(self):
        """测试获取高严重程度风险因素"""
        high_factor = RiskFactor(
            factor_id="rf_high",
            description="严重漏洞",
            severity=RiskLevel.HIGH,
        )

        critical_factor = RiskFactor(
            factor_id="rf_critical",
            description="致命漏洞",
            severity=RiskLevel.CRITICAL,
        )

        low_factor = RiskFactor(
            factor_id="rf_low",
            description="轻微问题",
            severity=RiskLevel.LOW,
        )

        medium_factor = RiskFactor(
            factor_id="rf_medium",
            description="中等问题",
            severity=RiskLevel.MEDIUM,
        )

        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.HIGH,
            risk_factors=[high_factor, critical_factor, low_factor, medium_factor],
        )

        high_factors = assessment.get_high_severity_factors()

        assert len(high_factors) == 2
        factor_ids = [f.factor_id for f in high_factors]
        assert "rf_high" in factor_ids
        assert "rf_critical" in factor_ids
        assert "rf_low" not in factor_ids
        assert "rf_medium" not in factor_ids

    def test_get_high_severity_factors_empty(self):
        """测试无高严重程度风险因素"""
        low_factor = RiskFactor(
            factor_id="rf_low",
            description="轻微问题",
            severity=RiskLevel.LOW,
        )

        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.LOW,
            risk_factors=[low_factor],
        )

        high_factors = assessment.get_high_severity_factors()
        assert len(high_factors) == 0

    def test_multiple_blocking_conditions(self):
        """测试多个阻塞条件"""
        blocking1 = BlockingCondition(
            condition_id="bc_001",
            description="安全审计未通过",
            blocking_reason="必须通过安全审计",
        )

        blocking2 = BlockingCondition(
            condition_id="bc_002",
            description="缺少性能测试报告",
            blocking_reason="需要提供性能测试报告",
        )

        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.CRITICAL,
            blocking_conditions=[blocking1, blocking2],
        )

        assert assessment.has_blocking_conditions() is True
        assert len(assessment.blocking_conditions) == 2

    def test_multiple_evidence_types(self):
        """测试多种证据类型"""
        fact = ExpertEvidence(
            expert_id="expert_001",
            expert_domain="security",
            evidence_text="发现SQL注入漏洞",
            evidence_type="fact",
        )

        opinion = ExpertEvidence(
            expert_id="expert_002",
            expert_domain="architecture",
            evidence_text="架构设计需要优化",
            evidence_type="opinion",
        )

        concern = ExpertEvidence(
            expert_id="expert_003",
            expert_domain="performance",
            evidence_text="可能存在性能瓶颈",
            evidence_type="concern",
        )

        recommendation = ExpertEvidence(
            expert_id="expert_004",
            expert_domain="security",
            evidence_text="建议添加参数化查询",
            evidence_type="recommendation",
        )

        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.HIGH,
            supporting_evidence=[fact, opinion, concern, recommendation],
        )

        assert len(assessment.supporting_evidence) == 4
        types = [e.evidence_type for e in assessment.supporting_evidence]
        assert "fact" in types
        assert "opinion" in types
        assert "concern" in types
        assert "recommendation" in types


__all__ = [
    "TestRiskFactor",
    "TestBlockingCondition",
    "TestExpertEvidence",
    "TestScenarioPriorRisk",
    "TestStructuredRiskAssessment",
]