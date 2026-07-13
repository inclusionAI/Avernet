"""
FusionRecommendationService 测试

测试 Fusion Recommendation 服务层。
"""

import pytest

from src.application.services.fusion_recommendation_service import FusionRecommendationService
from src.infra.llm.providers.fake_provider import FakeLLMProvider
from src.application.services.llm_gateway_service import LLMGatewayService
from src.domain.models.fusion_result import Perspective
from src.domain.models.fusion_recommendation import Decision


class TestFusionRecommendationService:
    """FusionRecommendationService 测试"""

    def test_create_service(self):
        """测试创建服务"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)
        service = FusionRecommendationService(gateway=gateway)

        assert service is not None

    def test_generate_recommendation_basic(self):
        """测试生成基本建议"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)
        service = FusionRecommendationService(gateway=gateway)

        perspectives = [
            Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="从数据库角度可行",
                confidence=0.85,
                status="completed",
            ),
        ]

        result = service.generate(
            question="这个方案是否可行?",
            driver_bot_id="dba",
            perspectives=perspectives,
        )

        assert result is not None
        assert result.summary is not None
        assert result.decision in [Decision.YES, Decision.NO, Decision.CONDITIONAL_YES, Decision.NEEDS_MORE_INFORMATION]
        assert 0 <= result.confidence <= 1

    def test_generate_recommendation_with_multiple_perspectives(self):
        """测试生成多视角建议"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)
        service = FusionRecommendationService(gateway=gateway)

        perspectives = [
            Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="数据库角度可行",
                confidence=0.85,
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="安全角度需要补充审计",
                confidence=0.7,
                status="completed",
            ),
            Perspective(
                participant_id="ops",
                participant_type="bot",
                role="consultant",
                summary="运维角度需要监控方案",
                confidence=0.75,
                status="completed",
            ),
        ]

        result = service.generate(
            question="方案评估",
            driver_bot_id="dba",
            perspectives=perspectives,
        )

        assert result is not None
        assert len(result.reasoning) >= 0
        assert isinstance(result.risks, list)
        assert isinstance(result.next_actions, list)

    def test_generate_recommendation_with_partial_success(self):
        """测试部分成功场景"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)
        service = FusionRecommendationService(gateway=gateway)

        perspectives = [
            Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="可行",
                confidence=0.8,
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="",
                confidence=None,
                status="timed_out",
            ),
        ]

        result = service.generate(
            question="测试",
            driver_bot_id="dba",
            perspectives=perspectives,
            partial_success=True,
            warnings=["security timed out"],
        )

        assert result is not None
        # 应该体现部分成功的情况
        assert isinstance(result.missing_information, list)

    def test_generate_recommendation_with_errors(self):
        """测试有错误的场景"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)
        service = FusionRecommendationService(gateway=gateway)

        perspectives = [
            Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="可行",
                confidence=0.8,
                status="completed",
            ),
        ]

        result = service.generate(
            question="测试",
            driver_bot_id="dba",
            perspectives=perspectives,
            errors=["Failed to collect perspective from security"],
        )

        assert result is not None


class TestFusionRecommendationServiceIntegration:
    """FusionRecommendationService 集成测试"""

    def test_full_flow(self):
        """测试完整流程"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)
        service = FusionRecommendationService(gateway=gateway)

        # 模拟 G1 Fusion 的输入
        perspectives = [
            Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="从数据库角度，该方案会增加索引维护成本，但整体可行。",
                confidence=0.85,
                evidence=["涉及读多写少场景", "索引命中率可接受"],
                status="completed",
            ),
            Perspective(
                participant_id="security",
                participant_type="bot",
                role="consultant",
                summary="从安全角度，需要补充权限校验与审计日志。",
                confidence=0.80,
                evidence=["缺少审计日志"],
                status="completed",
            ),
        ]

        result = service.generate(
            question="这个方案是否可以在下个迭代上线?",
            driver_bot_id="dba",
            perspectives=perspectives,
            partial_success=False,
        )

        # 验证结果结构
        assert result.summary is not None
        assert result.decision is not None
        assert isinstance(result.reasoning, list)
        assert isinstance(result.risks, list)
        assert isinstance(result.missing_information, list)
        assert isinstance(result.next_actions, list)
        assert 0 <= result.confidence <= 1