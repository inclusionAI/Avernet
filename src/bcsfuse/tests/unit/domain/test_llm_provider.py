"""
LLMProvider 协议测试

测试 LLM Provider 协议的定义和行为。
"""

import pytest
from typing import Protocol, runtime_checkable

from src.domain.services.llm_provider import LLMProvider
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_response import LLMResponse
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType


class TestLLMProviderProtocol:
    """LLMProvider 协议测试"""

    def test_is_protocol(self):
        """测试 LLMProvider 是 Protocol"""
        assert issubclass(LLMProvider, Protocol)

    def test_protocol_has_generate_method(self):
        """测试协议有 generate 方法"""
        # 检查协议定义了 generate 方法
        assert hasattr(LLMProvider, 'generate')

    def test_concrete_implementation(self):
        """测试具体实现满足协议"""
        class FakeProvider:
            """测试用的 fake provider"""

            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    provider_id="fake",
                    model_id="fake-model",
                    raw_text="test response",
                    parse_success=True,
                    latency_ms=100,
                    finish_reason="stop",
                )

        provider = FakeProvider()

        # 应该可以被识别为协议实现
        assert isinstance(provider, LLMProvider)

    def test_generate_signature(self):
        """测试 generate 方法签名"""
        class FakeProvider:
            def generate(self, request: LLMRequest) -> LLMResponse:
                task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
                return LLMResponse(
                    provider_id="fake",
                    model_id="fake-model",
                    raw_text=f"Response to: {request.user_prompt}",
                    parse_success=True,
                    latency_ms=50,
                    finish_reason="stop",
                )

        provider = FakeProvider()

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response = provider.generate(request)

        assert isinstance(response, LLMResponse)
        assert "test" in response.raw_text


class TestLLMProviderIntegration:
    """LLMProvider 协议集成测试"""

    def test_provider_can_be_used_polymorphically(self):
        """测试 provider 可以多态使用"""
        class ProviderA:
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    provider_id="provider-a",
                    model_id="model-a",
                    raw_text="Response from A",
                    parse_success=True,
                    latency_ms=100,
                    finish_reason="stop",
                )

        class ProviderB:
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    provider_id="provider-b",
                    model_id="model-b",
                    raw_text="Response from B",
                    parse_success=True,
                    latency_ms=200,
                    finish_reason="stop",
                )

        def use_provider(provider: LLMProvider, request: LLMRequest) -> LLMResponse:
            return provider.generate(request)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response_a = use_provider(ProviderA(), request)
        response_b = use_provider(ProviderB(), request)

        assert response_a.provider_id == "provider-a"
        assert response_b.provider_id == "provider-b"