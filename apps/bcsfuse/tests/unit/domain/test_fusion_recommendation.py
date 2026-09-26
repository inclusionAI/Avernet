"""
FusionRecommendation 领域模型测试

测试融合建议输出模型的验证和行为。
"""

import pytest
from pydantic import ValidationError

from src.domain.models.fusion_recommendation import (
    FusionRecommendation,
    Decision,
)


class TestDecision:
    """Decision 枚举测试"""

    def test_decision_values(self):
        """测试决策枚举值"""
        assert Decision.YES == "yes"
        assert Decision.NO == "no"
        assert Decision.CONDITIONAL_YES == "conditional_yes"
        assert Decision.NEEDS_MORE_INFORMATION == "needs_more_information"

    def test_decision_from_string(self):
        """测试从字符串创建枚举"""
        assert Decision("yes") == Decision.YES
        assert Decision("conditional_yes") == Decision.CONDITIONAL_YES


class TestFusionRecommendation:
    """FusionRecommendation 模型测试"""

    def test_create_yes_recommendation(self):
        """测试创建 yes 决策的建议"""
        rec = FusionRecommendation(
            summary="各方视角一致认为方案可行。",
            decision=Decision.YES,
            reasoning=["所有参与者都表示支持", "没有发现明显风险"],
            risks=[],
            missing_information=[],
            next_actions=["可以开始执行"],
            confidence=0.9,
        )

        assert rec.summary == "各方视角一致认为方案可行。"
        assert rec.decision == Decision.YES
        assert len(rec.reasoning) == 2
        assert rec.confidence == 0.9

    def test_create_conditional_yes_recommendation(self):
        """测试创建 conditional_yes 决策的建议"""
        rec = FusionRecommendation(
            summary="方案基本可行，但需要补充安全审计。",
            decision=Decision.CONDITIONAL_YES,
            reasoning=["DBA 认为可行", "安全团队需要补充审计"],
            risks=["缺少审计日志可能导致合规问题"],
            missing_information=[],
            next_actions=["补充审计日志", "再次评估"],
            confidence=0.7,
        )

        assert rec.decision == Decision.CONDITIONAL_YES
        assert len(rec.risks) == 1
        assert len(rec.next_actions) == 2

    def test_create_needs_more_information_recommendation(self):
        """测试创建 needs_more_information 决策的建议"""
        rec = FusionRecommendation(
            summary="信息不足，无法做出判断。",
            decision=Decision.NEEDS_MORE_INFORMATION,
            reasoning=["缺少关键视角"],
            risks=["安全视角缺失"],
            missing_information=["安全评估报告", "性能测试结果"],
            next_actions=["补充安全评估", "执行性能测试"],
            confidence=0.3,
        )

        assert rec.decision == Decision.NEEDS_MORE_INFORMATION
        assert len(rec.missing_information) == 2
        assert rec.confidence < 0.5

    def test_create_no_recommendation(self):
        """测试创建 no 决策的建议"""
        rec = FusionRecommendation(
            summary="方案存在严重风险，不建议执行。",
            decision=Decision.NO,
            reasoning=["安全风险过高", "成本超出预算"],
            risks=["数据泄露风险", "成本超支"],
            missing_information=[],
            next_actions=["重新设计方案"],
            confidence=0.85,
        )

        assert rec.decision == Decision.NO
        assert len(rec.risks) == 2

    def test_required_fields(self):
        """测试必填字段"""
        with pytest.raises(ValidationError) as exc_info:
            FusionRecommendation()

        errors = exc_info.value.errors()
        error_fields = {e["loc"][0] for e in errors}
        # summary, decision, confidence are required
        # reasoning, risks, missing_information, next_actions have default empty lists
        assert "summary" in error_fields
        assert "decision" in error_fields
        assert "confidence" in error_fields

    def test_summary_not_empty(self):
        """测试 summary 不能为空"""
        with pytest.raises(ValidationError):
            FusionRecommendation(
                summary="",
                decision=Decision.YES,
                reasoning=[],
                risks=[],
                missing_information=[],
                next_actions=[],
                confidence=0.9,
            )

    def test_confidence_range(self):
        """测试 confidence 范围验证"""
        # 有效范围
        for conf in [0.0, 0.5, 1.0]:
            rec = FusionRecommendation(
                summary="test",
                decision=Decision.YES,
                reasoning=[],
                risks=[],
                missing_information=[],
                next_actions=[],
                confidence=conf,
            )
            assert rec.confidence == conf

        # 超出范围
        with pytest.raises(ValidationError):
            FusionRecommendation(
                summary="test",
                decision=Decision.YES,
                reasoning=[],
                risks=[],
                missing_information=[],
                next_actions=[],
                confidence=-0.1,
            )

        with pytest.raises(ValidationError):
            FusionRecommendation(
                summary="test",
                decision=Decision.YES,
                reasoning=[],
                risks=[],
                missing_information=[],
                next_actions=[],
                confidence=1.1,
            )

    def test_list_fields_default_empty(self):
        """测试列表字段默认为空列表"""
        rec = FusionRecommendation(
            summary="test",
            decision=Decision.YES,
            reasoning=[],
            risks=[],
            missing_information=[],
            next_actions=[],
            confidence=0.9,
        )

        assert rec.reasoning == []
        assert rec.risks == []
        assert rec.missing_information == []
        assert rec.next_actions == []

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(ValidationError):
            FusionRecommendation(
                summary="test",
                decision=Decision.YES,
                reasoning=[],
                risks=[],
                missing_information=[],
                next_actions=[],
                confidence=0.9,
                unknown_field="value",  # type: ignore
            )

    def test_model_dump(self):
        """测试模型序列化"""
        rec = FusionRecommendation(
            summary="test summary",
            decision=Decision.CONDITIONAL_YES,
            reasoning=["reason 1", "reason 2"],
            risks=["risk 1"],
            missing_information=["info 1"],
            next_actions=["action 1"],
            confidence=0.75,
        )

        data = rec.model_dump()

        assert data["summary"] == "test summary"
        assert data["decision"] == "conditional_yes"
        assert data["reasoning"] == ["reason 1", "reason 2"]
        assert data["risks"] == ["risk 1"]
        assert data["missing_information"] == ["info 1"]
        assert data["next_actions"] == ["action 1"]
        assert data["confidence"] == 0.75

    def test_string_decision_conversion(self):
        """测试字符串 decision 自动转换"""
        rec = FusionRecommendation(
            summary="test",
            decision="conditional_yes",  # type: ignore
            reasoning=[],
            risks=[],
            missing_information=[],
            next_actions=[],
            confidence=0.7,
        )

        assert rec.decision == Decision.CONDITIONAL_YES