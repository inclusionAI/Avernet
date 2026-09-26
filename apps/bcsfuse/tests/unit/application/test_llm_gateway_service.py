"""
LLMGatewayService 测试

测试 LLM Gateway 服务层。
"""

import pytest

from src.application.services.llm_gateway_service import LLMGatewayService
from src.infra.llm.providers.fake_provider import FakeLLMProvider
from src.infra.llm.routing.static_llm_router import StaticLLMRouter
from src.infra.llm.config.llm_settings import LLMSettings
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
from src.domain.models.llm_response import FinishReason


class TestLLMGatewayService:
    """LLMGatewayService 测试"""

    def test_create_gateway_with_fake_provider(self):
        """测试使用 Fake Provider 创建 Gateway"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)

        assert gateway is not None

    def test_generate_simple_request(self):
        """测试生成简单请求"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="请总结以下内容",
        )

        response = gateway.generate(request)

        assert response is not None
        assert response.finish_reason == FinishReason.STOP
        assert len(response.raw_text) > 0

    def test_generate_fusion_recommendation(self):
        """测试生成 Fusion Recommendation"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)

        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="请基于以下视角输出建议",
            expected_schema_name="FusionRecommendation",
        )

        response = gateway.generate(request)

        assert response.parse_success is True
        assert response.structured_data is not None
        assert "decision" in response.structured_data

    def test_generate_with_router(self):
        """测试使用 Router 生成"""
        provider = FakeLLMProvider()
        router = StaticLLMRouter()
        gateway = LLMGatewayService(provider=provider, router=router)

        task_spec = LLMTaskSpec(task_type=TaskType.FUSION_RECOMMENDATION)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response = gateway.generate(request)

        assert response is not None

    def test_generate_parse_failure_handling(self):
        """测试解析失败处理"""
        provider = FakeLLMProvider(simulate_parse_failure=True)
        gateway = LLMGatewayService(provider=provider)

        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            expected_schema_name="FusionRecommendation",
        )

        response = gateway.generate(request)

        assert response.parse_success is False
        assert len(response.warnings) > 0

    def test_generate_error_handling(self):
        """测试错误处理"""
        provider = FakeLLMProvider(simulate_error=True)
        gateway = LLMGatewayService(provider=provider)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response = gateway.generate(request)

        assert response.finish_reason == FinishReason.ERROR
        assert len(response.errors) > 0


class TestLLMGatewayServiceWithSettings:
    """LLMGatewayService 使用 Settings 测试"""

    def test_create_with_settings(self):
        """测试使用 Settings 创建 Gateway"""
        settings = LLMSettings(
            base_url="https://test.api",
            default_timeout_ms=20000,
        )
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider, settings=settings)

        assert gateway is not None

    def test_generate_preserves_request_metadata(self):
        """测试生成保留请求元数据"""
        provider = FakeLLMProvider()
        gateway = LLMGatewayService(provider=provider)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            metadata={"trace_id": "trace-123"},  # type: ignore
        )

        response = gateway.generate(request)

        assert response is not None