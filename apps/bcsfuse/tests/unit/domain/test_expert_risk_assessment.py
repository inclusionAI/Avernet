"""
Tests for Expert Risk Assessment (G5)

G5: Expert Diagnosis Layer

测试 G5 专家诊断的风险评估模型。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.models.expert_risk_assessment import (
    RiskLevel,
    RiskAssessment,
)


class TestRiskLevel:
    """风险等级枚举测试"""

    def test_risk_level_enum_values(self):
        """测试风险等级枚举值"""
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"

    def test_risk_level_enum_count(self):
        """测试风险等级枚举数量"""
        assert len(RiskLevel) == 4

    def test_risk_level_string_conversion(self):
        """测试风险等级字符串转换"""
        assert RiskLevel("low") == RiskLevel.LOW
        assert RiskLevel("medium") == RiskLevel.MEDIUM
        assert RiskLevel("high") == RiskLevel.HIGH
        assert RiskLevel("critical") == RiskLevel.CRITICAL

    def test_risk_level_invalid_value(self):
        """测试无效风险等级"""
        with pytest.raises(ValueError):
            RiskLevel("invalid")


class TestDomain:
    """领域枚举测试"""

    def test_domain_enum_values(self):
        """测试领域枚举值"""
        from src.domain.models.expert_risk_assessment import Domain

        assert Domain.SECURITY == "security"
        assert Domain.LEGAL == "legal"
        assert Domain.DATABASE == "database"
        assert Domain.OPS == "ops"
        assert Domain.TECH == "tech"
        assert Domain.ARCHITECTURE == "architecture"

    def test_domain_enum_count(self):
        """测试领域枚举数量"""
        from src.domain.models.expert_risk_assessment import Domain

        assert len(Domain) == 6

    def test_domain_string_conversion(self):
        """测试领域字符串转换"""
        from src.domain.models.expert_risk_assessment import Domain

        assert Domain("security") == Domain.SECURITY
        assert Domain("legal") == Domain.LEGAL
        assert Domain("database") == Domain.DATABASE

    def test_domain_invalid_value(self):
        """测试无效领域"""
        from src.domain.models.expert_risk_assessment import Domain

        with pytest.raises(ValueError):
            Domain("invalid_domain")


class TestRiskAssessment:
    """风险评估模型测试"""

    def test_risk_assessment_overall_required(self):
        """测试 overall 字段必填"""
        with pytest.raises(ValidationError):
            RiskAssessment(categories={"security": "high"})

    def test_risk_assessment_overall_valid(self):
        """测试 overall 字段有效值"""
        assessment = RiskAssessment(overall=RiskLevel.HIGH)
        assert assessment.overall == RiskLevel.HIGH

    def test_risk_assessment_categories_optional(self):
        """测试 categories 字段可选"""
        assessment = RiskAssessment(overall=RiskLevel.LOW)
        assert assessment.categories == {}

    def test_risk_assessment_categories_dict(self):
        """测试 categories 字典类型"""
        assessment = RiskAssessment(
            overall=RiskLevel.MEDIUM,
            categories={
                "security": RiskLevel.HIGH,
                "legal": RiskLevel.MEDIUM,
                "technical": RiskLevel.LOW,
            },
        )
        assert assessment.categories["security"] == RiskLevel.HIGH
        assert assessment.categories["legal"] == RiskLevel.MEDIUM
        assert assessment.categories["technical"] == RiskLevel.LOW

    def test_risk_assessment_empty_categories_allowed(self):
        """测试空 categories 允许"""
        assessment = RiskAssessment(overall=RiskLevel.LOW, categories={})
        assert assessment.categories == {}

    def test_risk_assessment_categories_string_values(self):
        """测试 categories 支持字符串值"""
        assessment = RiskAssessment(
            overall="high",
            categories={"security": "high", "legal": "medium"},
        )
        assert assessment.overall == RiskLevel.HIGH
        assert assessment.categories["security"] == RiskLevel.HIGH
        assert assessment.categories["legal"] == RiskLevel.MEDIUM

    def test_risk_assessment_model_dump(self):
        """测试 model_dump 序列化"""
        assessment = RiskAssessment(
            overall=RiskLevel.HIGH,
            categories={"security": RiskLevel.CRITICAL},
        )
        data = assessment.model_dump()
        assert data["overall"] == "high"
        assert data["categories"]["security"] == "critical"

    def test_risk_assessment_extra_forbidden(self):
        """测试额外字段禁止"""
        with pytest.raises(ValidationError):
            RiskAssessment(
                overall=RiskLevel.LOW,
                extra_field="not_allowed",  # type: ignore
            )

    def test_risk_assessment_all_risk_levels(self):
        """测试所有风险等级"""
        for level in RiskLevel:
            assessment = RiskAssessment(overall=level)
            assert assessment.overall == level

    def test_risk_assessment_multiple_categories(self):
        """测试多个领域风险"""
        assessment = RiskAssessment(
            overall=RiskLevel.HIGH,
            categories={
                "security": RiskLevel.CRITICAL,
                "legal": RiskLevel.HIGH,
                "dba": RiskLevel.MEDIUM,
                "ops": RiskLevel.LOW,
            },
        )
        assert len(assessment.categories) == 4
        assert assessment.overall == RiskLevel.HIGH


class TestRiskAssessmentBusinessLogic:
    """风险评估业务逻辑测试"""

    def test_overall_reflects_highest_category_risk(self):
        """测试整体风险应反映最高分类风险（业务逻辑验证）"""
        # 这是一项业务规则验证：整体风险应该是各领域风险的最高值
        # 但模型本身不做这个计算，由服务层负责
        assessment = RiskAssessment(
            overall=RiskLevel.CRITICAL,
            categories={
                "security": RiskLevel.CRITICAL,
                "legal": RiskLevel.MEDIUM,
                "dba": RiskLevel.LOW,
            },
        )
        assert assessment.overall == RiskLevel.CRITICAL

    def test_single_category_scenario(self):
        """测试单领域场景"""
        assessment = RiskAssessment(
            overall=RiskLevel.MEDIUM,
            categories={"security": RiskLevel.MEDIUM},
        )
        assert len(assessment.categories) == 1
        assert assessment.categories["security"] == RiskLevel.MEDIUM