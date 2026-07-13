"""
G1 Fusion 与 LLM Gateway 接入测试

验证 GroupFusionService 与 FusionRecommendationService 的集成。
"""

import pytest

from src.application.services.group_fusion_service import GroupFusionService
from src.application.services.fusion_recommendation_service import FusionRecommendationService
from src.application.services.llm_gateway_service import LLMGatewayService
from src.infra.llm.providers.fake_provider import FakeLLMProvider
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.domain.models.fusion_result import Perspective
from src.domain.models.fusion_request import FusionRequest, FuseOptions


class MockPerspectiveProvider(PerspectiveProvider):
    """测试用的 Mock Perspective Provider"""

    def collect(self, context: PerspectiveContext) -> Perspective:
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"从 {context.participant_id} 角度，该方案可行。",
            confidence=0.85,
            status="completed",
        )


class TestG1LLMIntegration:
    """G1 与 LLM Gateway 集成测试"""

    def test_fusion_without_llm_service(self):
        """测试不使用 LLM 服务的融合（使用规则方法）"""
        provider = MockPerspectiveProvider()
        service = GroupFusionService(provider=provider)

        request = FusionRequest(
            question="这个方案是否可行?",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-test-001")

        assert result.recommendation is not None
        assert result.recommendation.summary is not None
        # 规则方法的 summary 会包含 "综合"
        assert "综合" in result.recommendation.summary or "可行" in result.recommendation.summary

    def test_fusion_with_llm_service(self):
        """测试使用 LLM 服务的融合"""
        # 创建 LLM Gateway
        llm_provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=llm_provider)
        rec_service = FusionRecommendationService(gateway=gateway)

        # 创建 Fusion Service 并注入 LLM 服务
        provider = MockPerspectiveProvider()
        service = GroupFusionService(
            provider=provider,
            recommendation_service=rec_service,
        )

        request = FusionRequest(
            question="这个方案是否可行?",
            participants=["dba", "security"],
        )

        result = service.fuse(request, group_id="grp-test-002")

        assert result.recommendation is not None
        assert result.recommendation.summary is not None
        assert result.recommendation.decision in ["yes", "no", "conditional_yes", "needs_more_information"]

    def test_fusion_llm_service_fallback_on_error(self):
        """测试 LLM 服务失败时回退到规则方法"""
        # 创建会失败的 LLM Provider
        llm_provider = FakeLLMProvider(simulate_error=True)
        gateway = LLMGatewayService(provider=llm_provider)
        rec_service = FusionRecommendationService(gateway=gateway)

        # 创建 Fusion Service 并注入 LLM 服务
        provider = MockPerspectiveProvider()
        service = GroupFusionService(
            provider=provider,
            recommendation_service=rec_service,
        )

        request = FusionRequest(
            question="这个方案是否可行?",
            participants=["dba"],
        )

        # 即使 LLM 失败，也应该返回有效的 recommendation（fallback）
        result = service.fuse(request, group_id="grp-test-003")

        assert result.recommendation is not None
        assert result.recommendation.summary is not None

    def test_fusion_no_recommendation_when_disabled(self):
        """测试禁用 recommendation 时不生成"""
        llm_provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=llm_provider)
        rec_service = FusionRecommendationService(gateway=gateway)

        provider = MockPerspectiveProvider()
        service = GroupFusionService(
            provider=provider,
            recommendation_service=rec_service,
        )

        request = FusionRequest(
            question="这个方案是否可行?",
            participants=["dba"],
            options=FuseOptions(include_recommendation=False),
        )

        result = service.fuse(request, group_id="grp-test-004")

        assert result.recommendation is None

    def test_fusion_decision_consistency(self):
        """测试 LLM 生成的 decision 枚举值与模型一致"""
        llm_provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=llm_provider)
        rec_service = FusionRecommendationService(gateway=gateway)

        provider = MockPerspectiveProvider()
        service = GroupFusionService(
            provider=provider,
            recommendation_service=rec_service,
        )

        request = FusionRequest(
            question="测试决策一致性",
            participants=["dba", "security", "ops"],
        )

        result = service.fuse(request, group_id="grp-test-005")

        assert result.recommendation is not None
        # decision 必须是有效的枚举值
        valid_decisions = ["yes", "no", "conditional_yes", "needs_more_information"]
        assert result.recommendation.decision in valid_decisions


class TestG1LLMIntegrationFullFlow:
    """G1 与 LLM Gateway 完整流程测试"""

    def test_full_flow_with_multiple_perspectives(self):
        """测试完整流程：多视角融合 + LLM recommendation"""
        # 准备 LLM 服务
        llm_provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=llm_provider)
        rec_service = FusionRecommendationService(gateway=gateway)

        # 准备 Perspective Provider
        provider = MockPerspectiveProvider()

        # 创建 Fusion Service
        service = GroupFusionService(
            provider=provider,
            recommendation_service=rec_service,
        )

        # 执行融合
        request = FusionRequest(
            question="这个方案是否可以在下个迭代上线?",
            participants=["dba", "security", "ops", "architect"],
        )

        result = service.fuse(request, group_id="grp-full-flow-001")

        # 验证结果
        assert result.group_id == "grp-full-flow-001"
        assert result.fusion_id.startswith("fus-")
        assert len(result.perspectives) == 4
        assert result.recommendation is not None
        assert result.recommendation.summary is not None
        assert result.recommendation.decision is not None
        assert isinstance(result.recommendation.risks, list)
        assert isinstance(result.recommendation.next_actions, list)
        assert result.partial_success is False  # 所有 perspective 都成功
        assert len(result.warnings) == 0
        assert len(result.errors) == 0