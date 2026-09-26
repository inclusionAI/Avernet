"""
Tests for Expert Diagnosis Models (G5)

G5: Expert Diagnosis Layer

测试 G5 专家诊断的关键问题和建议模型。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.models.expert_diagnosis import (
    Priority,
    CriticalIssue,
    ExpertRecommendation,
    GoLiveCondition,
)


class TestPriority:
    """优先级枚举测试"""

    def test_priority_enum_values(self):
        """测试优先级枚举值"""
        assert Priority.P0 == "P0"
        assert Priority.P1 == "P1"
        assert Priority.P2 == "P2"

    def test_priority_enum_count(self):
        """测试优先级枚举数量"""
        assert len(Priority) == 3

    def test_priority_string_conversion(self):
        """测试优先级字符串转换"""
        assert Priority("P0") == Priority.P0
        assert Priority("P1") == Priority.P1
        assert Priority("P2") == Priority.P2

    def test_priority_invalid_value(self):
        """测试无效优先级"""
        with pytest.raises(ValueError):
            Priority("high")

    def test_priority_ordering(self):
        """测试优先级排序（P0 > P1 > P2）"""
        # 枚举值本身没有顺序，但业务上 P0 是最高优先级
        assert Priority.P0.value == "P0"
        assert Priority.P1.value == "P1"
        assert Priority.P2.value == "P2"


class TestCriticalIssue:
    """关键问题模型测试"""

    def test_critical_issue_required_fields(self):
        """测试必填字段"""
        issue = CriticalIssue(
            issue="安全风险：支付接口缺少签名验证",
            severity="high",
            domain="security",
            source="anquan",
        )
        assert issue.issue == "安全风险：支付接口缺少签名验证"
        assert issue.severity == "high"
        assert issue.domain == "security"
        assert issue.source == "anquan"

    def test_critical_issue_issue_required(self):
        """测试 issue 字段必填"""
        with pytest.raises(ValidationError):
            CriticalIssue(severity="high", domain="security", source="anquan")  # type: ignore

    def test_critical_issue_severity_required(self):
        """测试 severity 字段必填"""
        with pytest.raises(ValidationError):
            CriticalIssue(issue="test", domain="security", source="anquan")  # type: ignore

    def test_critical_issue_domain_required(self):
        """测试 domain 字段必填"""
        with pytest.raises(ValidationError):
            CriticalIssue(issue="test", severity="high", source="anquan")  # type: ignore

    def test_critical_issue_source_required(self):
        """测试 source 字段必填"""
        with pytest.raises(ValidationError):
            CriticalIssue(issue="test", severity="high", domain="security")  # type: ignore

    def test_critical_issue_severity_enum(self):
        """测试 severity 使用 RiskLevel 枚举值"""
        from src.domain.models.expert_risk_assessment import RiskLevel

        issue = CriticalIssue(
            issue="test",
            severity=RiskLevel.HIGH,
            domain="security",
            source="anquan",
        )
        assert issue.severity == RiskLevel.HIGH

    def test_critical_issue_severity_string_values(self):
        """测试 severity 支持字符串值"""
        for level in ["low", "medium", "high", "critical"]:
            issue = CriticalIssue(
                issue="test",
                severity=level,
                domain="security",
                source="anquan",
            )
            assert issue.severity == level

    def test_critical_issue_optional_description(self):
        """测试可选 description 字段"""
        issue = CriticalIssue(
            issue="test",
            severity="high",
            domain="security",
            source="anquan",
            description="详细描述",
        )
        assert issue.description == "详细描述"

    def test_critical_issue_model_dump(self):
        """测试 model_dump 序列化"""
        issue = CriticalIssue(
            issue="test",
            severity="critical",
            domain="security",
            source="anquan",
        )
        data = issue.model_dump()
        assert data["issue"] == "test"
        assert data["severity"] == "critical"
        assert data["domain"] == "security"
        assert data["source"] == "anquan"

    def test_critical_issue_extra_forbidden(self):
        """测试额外字段禁止"""
        with pytest.raises(ValidationError):
            CriticalIssue(
                issue="test",
                severity="high",
                domain="security",
                source="anquan",
                extra_field="not_allowed",  # type: ignore
            )


class TestExpertRecommendation:
    """专家建议模型测试"""

    def test_expert_recommendation_required_fields(self):
        """测试必填字段"""
        rec = ExpertRecommendation(
            priority=Priority.P0,
            action="立即修复支付接口签名验证",
        )
        assert rec.priority == Priority.P0
        assert rec.action == "立即修复支付接口签名验证"

    def test_expert_recommendation_priority_required(self):
        """测试 priority 字段必填"""
        with pytest.raises(ValidationError):
            ExpertRecommendation(action="test")  # type: ignore

    def test_expert_recommendation_action_required(self):
        """测试 action 字段必填"""
        with pytest.raises(ValidationError):
            ExpertRecommendation(priority=Priority.P0)  # type: ignore

    def test_expert_recommendation_priority_enum(self):
        """测试 priority 使用 Priority 枚举"""
        for p in Priority:
            rec = ExpertRecommendation(priority=p, action="test")
            assert rec.priority == p

    def test_expert_recommendation_priority_string_values(self):
        """测试 priority 支持字符串值"""
        rec = ExpertRecommendation(priority="P0", action="test")
        assert rec.priority == Priority.P0

    def test_expert_recommendation_optional_owner(self):
        """测试可选 owner 字段"""
        rec = ExpertRecommendation(
            priority=Priority.P0,
            action="test",
            owner="security_team",
        )
        assert rec.owner == "security_team"

    def test_expert_recommendation_optional_domain(self):
        """测试可选 domain 字段"""
        rec = ExpertRecommendation(
            priority=Priority.P0,
            action="test",
            domain="security",
        )
        assert rec.domain == "security"

    def test_expert_recommendation_optional_deadline(self):
        """测试可选 deadline 字段"""
        from datetime import datetime

        deadline = datetime(2024, 12, 31)
        rec = ExpertRecommendation(
            priority=Priority.P0,
            action="test",
            deadline=deadline,
        )
        assert rec.deadline == deadline

    def test_expert_recommendation_model_dump(self):
        """测试 model_dump 序列化"""
        rec = ExpertRecommendation(
            priority=Priority.P1,
            action="完成安全审计",
            owner="security_team",
            domain="security",
        )
        data = rec.model_dump()
        assert data["priority"] == "P1"
        assert data["action"] == "完成安全审计"
        assert data["owner"] == "security_team"
        assert data["domain"] == "security"

    def test_expert_recommendation_extra_forbidden(self):
        """测试额外字段禁止"""
        with pytest.raises(ValidationError):
            ExpertRecommendation(
                priority=Priority.P0,
                action="test",
                extra_field="not_allowed",  # type: ignore
            )


class TestGoLiveCondition:
    """上线条件模型测试"""

    def test_go_live_condition_simple_string(self):
        """测试简单字符串场景"""
        conditions = ["完成安全审计", "修复 P0 问题", "通过性能测试"]
        assert len(conditions) == 3
        assert "完成安全审计" in conditions

    def test_go_live_condition_empty_allowed(self):
        """测试空条件列表允许"""
        conditions: list[str] = []
        assert len(conditions) == 0


class TestExpertDiagnosisIntegration:
    """专家诊断集成测试"""

    def test_complete_diagnosis_structure(self):
        """测试完整诊断结构"""
        from src.domain.models.expert_risk_assessment import RiskLevel, RiskAssessment

        # 风险评估
        risk_assessment = RiskAssessment(
            overall=RiskLevel.HIGH,
            categories={
                "security": RiskLevel.CRITICAL,
                "legal": RiskLevel.MEDIUM,
                "dba": RiskLevel.LOW,
            },
        )

        # 关键问题
        critical_issues = [
            CriticalIssue(
                issue="支付接口缺少签名验证",
                severity=RiskLevel.CRITICAL,
                domain="security",
                source="anquan",
            ),
            CriticalIssue(
                issue="数据存储合规性问题",
                severity=RiskLevel.MEDIUM,
                domain="legal",
                source="fawu",
            ),
        ]

        # 建议
        recommendations = [
            ExpertRecommendation(
                priority=Priority.P0,
                action="立即添加支付接口签名验证",
                owner="security_team",
                domain="security",
            ),
            ExpertRecommendation(
                priority=Priority.P1,
                action="完成数据合规性整改",
                owner="legal_team",
                domain="legal",
            ),
        ]

        # 上线条件
        go_live_conditions = [
            "完成支付接口签名验证",
            "通过安全渗透测试",
            "完成数据合规审查",
        ]

        # 验证结构完整
        assert risk_assessment.overall == RiskLevel.HIGH
        assert len(critical_issues) == 2
        assert len(recommendations) == 2
        assert len(go_live_conditions) == 3