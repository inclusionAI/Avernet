"""
Feature Flag Switch Matrix Tests

测试 G5 V2 Feature Flags 的各种组合场景。

测试开关矩阵：
| ENABLE_TAXONOMY | ENABLE_G5_STRUCTURED | 预期行为 |
|-----------------|---------------------|----------|
| False           | False               | 旧逻辑，无新字段 |
| True            | False               | 关键词从配置读取，structured_risk=None |
| False           | True                | 关键词用 legacy，structured_risk 有值 |
| True            | True                | V2 完整逻辑 |
"""

import pytest
from unittest.mock import patch, MagicMock
import os

from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
from src.domain.models.fusion_result import Perspective
from src.domain.models.expert_risk_assessment import RiskLevel
from src.infra.config.feature_flags import FeatureFlags
from src.domain.taxonomy import reset_taxonomy_registry


class TestFeatureFlagMatrix:
    """Feature Flag 开关矩阵测试"""

    def setup_method(self):
        """每个测试前重置状态"""
        FeatureFlags.reset()
        reset_taxonomy_registry()

    def teardown_method(self):
        """每个测试后清理"""
        FeatureFlags.reset()
        reset_taxonomy_registry()

    def _create_test_perspective(self, summary: str = "存在安全漏洞") -> Perspective:
        """创建测试视角"""
        return Perspective(
            participant_id="expert_test",
            participant_type="bot",
            role="expert",
            summary=summary,
            confidence=0.9,
            evidence=["测试证据"],
            status="completed",
        )

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "false",
        "ENABLE_G5_STRUCTURED_RISK": "false",
    })
    def test_flags_all_off(self):
        """测试所有 V2 flags 关闭 - 旧逻辑，无新字段"""
        FeatureFlags.reset()
        service = ExpertDiagnosisService()

        result = service.diagnose(
            question="数据泄露风险评估",
            perspectives=[self._create_test_perspective()],
        )

        # 验证旧逻辑
        assert result.risk_assessment is not None
        # V2 字段为 None
        assert result.structured_risk is None

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "true",
        "ENABLE_G5_STRUCTURED_RISK": "false",
    })
    def test_only_taxonomy_on(self):
        """测试仅 Taxonomy 开启 - 关键词从配置读取"""
        FeatureFlags.reset()
        reset_taxonomy_registry()
        service = ExpertDiagnosisService()

        # 验证 taxonomy 启用
        assert FeatureFlags.is_taxonomy_registry_enabled() is True
        assert FeatureFlags.is_g5_structured_risk_enabled() is False

        result = service.diagnose(
            question="数据泄露风险评估",
            perspectives=[self._create_test_perspective()],
        )

        # 风险评估使用 TaxonomyRegistry 关键词
        assert result.risk_assessment is not None
        assert result.risk_assessment.overall == RiskLevel.CRITICAL
        # V2 字段为 None
        assert result.structured_risk is None

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "false",
        "ENABLE_G5_STRUCTURED_RISK": "true",
    })
    def test_only_structured_risk_on(self):
        """测试仅 G5 Structured Risk 开启 - 使用 legacy 关键词"""
        FeatureFlags.reset()
        reset_taxonomy_registry()
        service = ExpertDiagnosisService()

        # 验证 flags
        assert FeatureFlags.is_taxonomy_registry_enabled() is False
        assert FeatureFlags.is_g5_structured_risk_enabled() is True

        result = service.diagnose(
            question="数据泄露风险评估",
            perspectives=[self._create_test_perspective()],
        )

        # 风险评估正常
        assert result.risk_assessment is not None
        # V2 结构化风险评估有值
        assert result.structured_risk is not None
        assert result.structured_risk.risk_level == RiskLevel.CRITICAL

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "true",
        "ENABLE_G5_STRUCTURED_RISK": "true",
    })
    def test_both_flags_on(self):
        """测试所有 V2 flags 开启 - 完整 V2 逻辑"""
        FeatureFlags.reset()
        reset_taxonomy_registry()
        service = ExpertDiagnosisService()

        # 验证 flags
        assert FeatureFlags.is_taxonomy_registry_enabled() is True
        assert FeatureFlags.is_g5_structured_risk_enabled() is True

        result = service.diagnose(
            question="数据泄露风险评估",
            perspectives=[self._create_test_perspective()],
        )

        # 风险评估正常
        assert result.risk_assessment is not None
        # V2 结构化风险评估有值
        assert result.structured_risk is not None
        assert result.structured_risk.risk_level == RiskLevel.CRITICAL
        # 有风险因素
        assert len(result.structured_risk.risk_factors) > 0

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "true",
        "ENABLE_G5_STRUCTURED_RISK": "false",
    })
    def test_taxonomy_with_scenario_prior_risk(self):
        """测试 Taxonomy 启用时场景先验风险推断"""
        FeatureFlags.reset()
        reset_taxonomy_registry()
        service = ExpertDiagnosisService()

        result = service.diagnose(
            question="跨境支付合规评估",
            perspectives=[self._create_test_perspective("需要关注合规风险")],
        )

        # 应该识别为 HIGH 风险
        assert result.risk_assessment.overall in [RiskLevel.HIGH, RiskLevel.CRITICAL]


class TestStructuredRiskAssessmentOutput:
    """结构化风险评估输出测试"""

    def setup_method(self):
        """每个测试前重置"""
        FeatureFlags.reset()
        reset_taxonomy_registry()

    def teardown_method(self):
        """每个测试后清理"""
        FeatureFlags.reset()
        reset_taxonomy_registry()

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "true",
        "ENABLE_G5_STRUCTURED_RISK": "true",
        "ENABLE_G5_SCENARIO_PRIOR_RISK": "true",
    })
    def test_structured_risk_has_all_fields(self):
        """测试结构化风险评估包含所有字段"""
        FeatureFlags.reset()
        reset_taxonomy_registry()
        service = ExpertDiagnosisService()

        perspective = Perspective(
            participant_id="expert_security",
            participant_type="bot",
            role="expert",
            summary="存在严重安全漏洞，必须立即修复",
            confidence=0.95,
            evidence=["SQL注入风险", "数据泄露风险"],
            status="completed",
        )

        result = service.diagnose(
            question="数据泄露事件风险评估",
            perspectives=[perspective],
        )

        # 验证 structured_risk 存在
        assert result.structured_risk is not None

        # 验证所有字段
        structured = result.structured_risk
        assert structured.risk_level == RiskLevel.CRITICAL
        assert structured.baseline_risk is not None
        assert len(structured.risk_factors) > 0
        assert structured.decision_rationale != ""
        # 场景先验风险
        assert structured.scenario_prior_risk is not None
        assert structured.scenario_prior_risk.scenario_type == "data_leakage"

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "true",
        "ENABLE_G5_STRUCTURED_RISK": "true",
        "ENABLE_G5_SCENARIO_PRIOR_RISK": "false",
    })
    def test_structured_risk_without_scenario_prior(self):
        """测试结构化风险评估无场景先验风险（flag 关闭）"""
        FeatureFlags.reset()
        reset_taxonomy_registry()
        service = ExpertDiagnosisService()

        result = service.diagnose(
            question="数据泄露风险评估",
            perspectives=[Perspective(
                participant_id="expert_test",
                participant_type="bot",
                role="expert",
                summary="存在风险",
                status="completed",
            )],
        )

        assert result.structured_risk is not None
        # 场景先验风险为 None（flag 关闭）
        assert result.structured_risk.scenario_prior_risk is None


class TestBackwardCompatibility:
    """向后兼容性测试"""

    def setup_method(self):
        FeatureFlags.reset()
        reset_taxonomy_registry()

    def teardown_method(self):
        FeatureFlags.reset()
        reset_taxonomy_registry()

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "true",
        "ENABLE_TAXONOMY_REGISTRY": "false",
        "ENABLE_G5_STRUCTURED_RISK": "false",
    })
    def test_old_client_ignores_new_fields(self):
        """测试旧客户端忽略新字段"""
        FeatureFlags.reset()
        service = ExpertDiagnosisService()

        result = service.diagnose(
            question="测试问题",
            perspectives=[Perspective(
                participant_id="expert",
                participant_type="bot",
                role="expert",
                summary="测试",
                status="completed",
            )],
        )

        # 旧字段存在
        assert hasattr(result, "risk_assessment")
        assert hasattr(result, "critical_issues")
        assert hasattr(result, "recommendations")
        assert hasattr(result, "go_live_conditions")
        assert hasattr(result, "summary")

        # 新字段存在但为 None
        assert hasattr(result, "structured_risk")
        assert result.structured_risk is None

    @patch.dict(os.environ, {
        "ENABLE_G5_EXPERT_DIAGNOSIS": "false",
    })
    def test_g5_disabled_fallback(self):
        """测试 G5 禁用时 fallback"""
        FeatureFlags.reset()
        service = ExpertDiagnosisService()

        result = service.diagnose(
            question="测试问题",
            perspectives=[Perspective(
                participant_id="expert",
                participant_type="bot",
                role="expert",
                summary="测试",
                status="completed",
            )],
        )

        # Fallback 结果
        assert result.fusion_mode == "expert_diagnosis"
        assert result.risk_assessment is None
        assert result.summary is not None