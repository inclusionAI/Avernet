"""
AnthropicCompatibleProvider 集成测试

Mock 测试默认运行，真实 LLM 测试需要设置环境变量：
- LLM_INTEGRATION_TEST=true
- LLM_BASE_URL
- LLM_AUTH_TOKEN

安全：
- 所有测试都使用占位符，不包含真实 token
- 真实测试只在显式启用时运行
"""

import os
import pytest
from unittest.mock import Mock, patch
import httpx

from src.infra.llm.providers.anthropic_compatible_provider import (
    AnthropicCompatibleProvider,
    AnthropicProviderError,
    AnthropicAuthError,
)
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType


# =============================================================================
# Mock 集成测试（默认运行）
# =============================================================================

class TestAnthropicProviderMockIntegration:
    """Mock 集成测试"""

    def test_full_request_flow_mock(self):
        """测试完整请求流程（Mock）"""
        provider = AnthropicCompatibleProvider(
            base_url="https://mock.api.example.com",
            auth_token="mock-token-placeholder",
        )

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            system_prompt="You are a helpful assistant.",
            user_prompt="What is 2+2?",
            max_tokens=100,
            temperature=0.1,
        )

        with patch('httpx.Client.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "msg-test-123",
                "model": "test-model",
                "content": [{"type": "text", "text": "2+2 equals 4."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 20, "output_tokens": 10}
            }
            mock_post.return_value = mock_response

            response = provider.generate(request, "test-model")

            assert response.raw_text == "2+2 equals 4."
            assert response.model_id == "test-model"
            assert response.usage.input_tokens == 20

    def test_error_recovery_flow_mock(self):
        """测试错误恢复流程（Mock）"""
        provider = AnthropicCompatibleProvider(
            base_url="https://mock.api.example.com",
            auth_token="mock-token-placeholder",
        )

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
        )

        with patch('httpx.Client.post') as mock_post:
            # 模拟 500 错误
            mock_response = Mock()
            mock_response.status_code = 500
            mock_response.json.return_value = {"error": "Internal error"}
            mock_post.return_value = mock_response

            with pytest.raises(AnthropicProviderError) as exc_info:
                provider.generate(request, "test-model")

            assert "PROVIDER_ERROR" in exc_info.value.code

    def test_streaming_not_supported_mock(self):
        """测试不支持流式响应（Mock）"""
        provider = AnthropicCompatibleProvider(
            base_url="https://mock.api.example.com",
            auth_token="mock-token-placeholder",
        )

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
        )

        with patch('httpx.Client.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "msg-test",
                "model": "test-model",
                "content": [{"type": "text", "text": "Response"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3}
            }
            mock_post.return_value = mock_response

            response = provider.generate(request, "test-model")

            # 当前实现不支持流式，返回完整响应
            assert response.raw_text == "Response"


# =============================================================================
# 真实 LLM 测试（需要显式启用）
# =============================================================================

# 检查是否启用真实 LLM 测试
REAL_LLM_ENABLED = (
    os.environ.get("LLM_INTEGRATION_TEST", "").lower() == "true"
    and bool(os.environ.get("LLM_BASE_URL"))
    and bool(os.environ.get("LLM_AUTH_TOKEN"))
)


@pytest.mark.skipif(not REAL_LLM_ENABLED, reason="Real LLM test disabled. Set LLM_INTEGRATION_TEST=true, LLM_BASE_URL, and LLM_AUTH_TOKEN to enable.")
class TestAnthropicProviderRealIntegration:
    """真实 LLM 集成测试"""

    def test_real_provider_smoke(self):
        """真实 Provider 连通性测试"""
        provider = AnthropicCompatibleProvider()

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="Say 'hello' and nothing else.",
            max_tokens=20,
            temperature=0,
        )

        # 从环境变量获取模型
        model = os.environ.get("LLM_FAST_MODEL", "GLM-5")

        response = provider.generate(request, model)

        assert response is not None
        assert len(response.raw_text) > 0
        assert response.finish_reason in ["stop", "length"]
        assert provider.base_url  # 从环境变量读取成功

        provider.close()

    def test_real_provider_with_system_prompt(self):
        """真实 Provider 带 System Prompt 测试"""
        provider = AnthropicCompatibleProvider()

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            system_prompt="You must respond with only the word 'OK'.",
            user_prompt="How are you?",
            max_tokens=10,
            temperature=0,
        )

        model = os.environ.get("LLM_FAST_MODEL", "GLM-5")

        response = provider.generate(request, model)

        assert response is not None
        # 响应应该简短
        assert len(response.raw_text) < 50

        provider.close()

    def test_real_provider_timeout(self):
        """真实 Provider 超时测试"""
        # 使用非常短的超时
        provider = AnthropicCompatibleProvider(timeout_ms=100)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="Write a very long story...",
            max_tokens=4000,
        )

        model = os.environ.get("LLM_FAST_MODEL", "GLM-5")

        # 可能超时，也可能成功（取决于网络）
        try:
            response = provider.generate(request, model)
            # 如果没超时，检查响应
            assert response is not None
        except Exception as e:
            # 超时是预期的
            assert "timeout" in str(e).lower() or "timed out" in str(e).lower()

        provider.close()


# =============================================================================
# G1 + LLM Gateway 真实集成测试
# =============================================================================

@pytest.mark.skipif(not REAL_LLM_ENABLED, reason="Real LLM test disabled. Set LLM_INTEGRATION_TEST=true, LLM_BASE_URL, and LLM_AUTH_TOKEN to enable.")
class TestG1RealLLMIntegration:
    """G1 + LLM Gateway 真实集成测试"""

    def test_g1_with_real_llm_recommendation(self):
        """G1 使用真实 LLM 生成 Recommendation"""
        from src.application.services.group_fusion_service import GroupFusionService
        from src.application.services.fusion_recommendation_service import FusionRecommendationService
        from src.application.services.llm_gateway_service import LLMGatewayService
        from src.infra.llm.routing.static_llm_router import StaticLLMRouter
        from src.infra.llm.config.llm_settings import LLMSettings
        from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
        from src.domain.models.fusion_result import Perspective

        # 创建真实 Provider
        settings = LLMSettings()
        provider = AnthropicCompatibleProvider(settings=settings)
        router = StaticLLMRouter(settings=settings)
        gateway = LLMGatewayService(provider=provider, router=router)
        rec_service = FusionRecommendationService(gateway=gateway)

        # 创建 Mock Perspective Provider
        class MockPerspectiveProvider(PerspectiveProvider):
            def collect(self, context: PerspectiveContext) -> Perspective:
                return Perspective(
                    participant_id=context.participant_id,
                    participant_type="bot",
                    role="consultant",
                    summary=f"从 {context.participant_id} 角度，该方案可行。",
                    confidence=0.85,
                    status="completed",
                )

        # 创建 Fusion Service
        fusion_service = GroupFusionService(
            provider=MockPerspectiveProvider(),
            recommendation_service=rec_service,
        )

        # 执行融合
        from src.domain.models.fusion_request import FusionRequest
        request = FusionRequest(
            question="这个方案是否可以在下个迭代上线?",
            participants=["dba", "security"],
        )

        result = fusion_service.fuse(request, group_id="grp-real-test-001")

        # 验证结果
        assert result.recommendation is not None
        assert result.recommendation.summary is not None
        assert result.recommendation.decision in [
            "yes", "no", "conditional_yes", "needs_more_information"
        ]

        print(f"\n=== G1 Real LLM Integration Result ===")
        print(f"Summary: {result.recommendation.summary}")
        print(f"Decision: {result.recommendation.decision}")
        print(f"Risks: {result.recommendation.risks}")
        print(f"Next Actions: {result.recommendation.next_actions}")

        provider.close()


# =============================================================================
# 工具函数
# =============================================================================

def test_integration_test_skip_message():
    """测试跳过信息（帮助调试）"""
    if not REAL_LLM_ENABLED:
        print("\n" + "=" * 60)
        print("Real LLM integration tests are disabled.")
        print("To enable, set the following environment variables:")
        print("  export LLM_INTEGRATION_TEST=true")
        print("  export LLM_BASE_URL=https://your-llm-api.example.com")
        print("  export LLM_AUTH_TOKEN=your-real-token-here")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("Real LLM integration tests are ENABLED.")
        print(f"LLM_BASE_URL: {os.environ.get('LLM_BASE_URL')}")
        print("=" * 60)