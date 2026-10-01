"""
Taxonomy Models Unit Tests

测试分类体系数据模型。
"""

import pytest
from pydantic import ValidationError

from src.domain.taxonomy.models import (
    DomainDefinition,
    ScenarioDefinition,
    RiskSignalDefinition,
    RiskLevelKeywords,
    DomainsConfig,
    ScenariosConfig,
    RiskSignalsConfig,
    TaxonomyConfig,
)


class TestDomainDefinition:
    """DomainDefinition 模型测试"""

    def test_create_minimal(self):
        """测试最小化创建"""
        domain = DomainDefinition(
            name="测试领域",
            description="测试描述",
        )
        assert domain.name == "测试领域"
        assert domain.description == "测试描述"
        assert domain.keywords == []
        assert domain.related_expert_types == []

    def test_create_full(self):
        """测试完整创建"""
        domain = DomainDefinition(
            name="安全",
            description="安全领域",
            keywords=["安全", "security", "auth"],
            related_expert_types=["安全专家"],
        )
        assert domain.name == "安全"
        assert len(domain.keywords) == 3
        assert "安全专家" in domain.related_expert_types


class TestScenarioDefinition:
    """ScenarioDefinition 模型测试"""

    def test_create_minimal(self):
        """测试最小化创建"""
        scenario = ScenarioDefinition(
            name="测试场景",
            description="测试描述",
        )
        assert scenario.name == "测试场景"
        assert scenario.category == "general"
        assert scenario.risk_weight == 0.5

    def test_create_with_risk_weight(self):
        """测试带风险权重创建"""
        scenario = ScenarioDefinition(
            name="高危场景",
            description="高风险场景",
            category="compliance",
            risk_weight=0.9,
        )
        assert scenario.risk_weight == 0.9
        assert scenario.category == "compliance"

    def test_invalid_risk_weight(self):
        """测试无效风险权重"""
        with pytest.raises(ValidationError):
            ScenarioDefinition(
                name="测试",
                description="测试",
                risk_weight=1.5,  # 超出范围
            )

        with pytest.raises(ValidationError):
            ScenarioDefinition(
                name="测试",
                description="测试",
                risk_weight=-0.1,  # 负数
            )


class TestRiskSignalDefinition:
    """RiskSignalDefinition 模型测试"""

    def test_create_minimal(self):
        """测试最小化创建"""
        signal = RiskSignalDefinition(
            name="测试信号",
            description="测试描述",
        )
        assert signal.weight == 1.0
        assert signal.keywords == []

    def test_create_with_keywords(self):
        """测试带关键词创建"""
        signal = RiskSignalDefinition(
            name="数据泄露",
            description="数据泄露事件",
            keywords=["数据泄露", "信息泄露", "隐私泄露"],
            weight=1.0,
        )
        assert len(signal.keywords) == 3
        assert signal.weight == 1.0


class TestRiskLevelKeywords:
    """RiskLevelKeywords 模型测试"""

    def test_create_empty(self):
        """测试空创建"""
        keywords = RiskLevelKeywords()
        assert keywords.critical == []
        assert keywords.high == []
        assert keywords.medium == []

    def test_create_with_keywords(self):
        """测试带关键词创建"""
        keywords = RiskLevelKeywords(
            critical=["严重", "critical"],
            high=["高风险", "漏洞"],
            medium=["中等", "关注"],
        )
        assert len(keywords.critical) == 2
        assert len(keywords.high) == 2
        assert len(keywords.medium) == 2


class TestTaxonomyConfig:
    """TaxonomyConfig 模型测试"""

    def test_create_empty(self):
        """测试空创建"""
        config = TaxonomyConfig()
        assert config.domains is not None
        assert config.scenarios is not None
        assert config.risk_signals is not None

    def test_create_with_components(self):
        """测试带组件创建"""
        domains = DomainsConfig()
        scenarios = ScenariosConfig()
        risk_signals = RiskSignalsConfig()

        config = TaxonomyConfig(
            domains=domains,
            scenarios=scenarios,
            risk_signals=risk_signals,
        )
        assert config.domains is domains
        assert config.scenarios is scenarios
        assert config.risk_signals is risk_signals


class TestStructuredRiskAssessment:
    """StructuredRiskAssessment 模型测试"""

    def test_import_and_create(self):
        """测试导入和创建"""
        from src.domain.models.structured_risk_assessment import (
            RiskFactor,
            BlockingCondition,
            ExpertEvidence,
            ScenarioPriorRisk,
            StructuredRiskAssessment,
        )
        from src.domain.models.expert_risk_assessment import RiskLevel

        # 创建风险因素
        factor = RiskFactor(
            factor_id="RF-001",
            description="安全漏洞",
            category="security",
            severity=RiskLevel.HIGH,
            likelihood="high",
            impact="high",
        )
        assert factor.factor_id == "RF-001"
        assert factor.severity == RiskLevel.HIGH

        # 创建阻塞条件
        blocking = BlockingCondition(
            condition_id="BC-001",
            description="需要修复安全漏洞",
            blocking_reason="存在高风险",
        )
        assert blocking.condition_id == "BC-001"

        # 创建专家证据
        evidence = ExpertEvidence(
            expert_id="expert_001",
            expert_domain="security",
            evidence_text="检测到漏洞",
            evidence_type="fact",
            confidence=0.9,
        )
        assert evidence.expert_id == "expert_001"

        # 创建完全结构化风险评估
        assessment = StructuredRiskAssessment(
            risk_level=RiskLevel.HIGH,
            baseline_risk=RiskLevel.MEDIUM,
            risk_factors=[factor],
            blocking_conditions=[blocking],
            supporting_evidence=[evidence],
            decision_rationale="存在高风险因素",
        )
        assert assessment.risk_level == RiskLevel.HIGH
        assert len(assessment.risk_factors) == 1
        assert assessment.has_blocking_conditions() is True