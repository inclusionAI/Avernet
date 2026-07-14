"""
FakeLLMProvider 测试

测试用于开发测试的 Fake LLM Provider。
"""

import pytest
import time

from src.infra.llm.providers.fake_provider import FakeLLMProvider
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_response import LLMResponse, FinishReason
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType


class TestFakeLLMProvider:
    """FakeLLMProvider 测试"""

    def test_create_provider(self):
        """测试创建 provider"""
        provider = FakeLLMProvider()
        assert provider is not None

    def test_generate_simple_response(self):
        """测试生成简单响应"""
        provider = FakeLLMProvider()
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="请总结以下内容",
        )

        response = provider.generate(request)

        assert isinstance(response, LLMResponse)
        assert response.provider_id == "fake"
        assert response.model_id == "fake-model"
        assert len(response.raw_text) > 0
        assert response.finish_reason == FinishReason.STOP

    def test_generate_fusion_recommendation(self):
        """测试生成融合建议响应"""
        provider = FakeLLMProvider()
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="请基于以下视角输出建议",
            expected_schema_name="FusionRecommendation",
        )

        response = provider.generate(request)

        assert response.parse_success is True
        assert response.structured_data is not None
        assert "decision" in response.structured_data
        assert "summary" in response.structured_data

    def test_generate_with_latency(self):
        """测试生成响应带延迟统计"""
        provider = FakeLLMProvider()
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
        )

        start = time.time()
        response = provider.generate(request)
        elapsed = time.time() - start

        assert response.latency_ms >= 0
        # fake provider 应该很快
        assert elapsed < 1.0

    def test_generate_with_custom_responses(self):
        """测试使用自定义响应"""
        custom_response = {
            "raw_text": "Custom response text",
            "structured_data": {"custom": "data"},
        }

        provider = FakeLLMProvider(responses=[custom_response])
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response = provider.generate(request)

        assert response.raw_text == "Custom response text"
        assert response.structured_data == {"custom": "data"}

    def test_generate_parse_failure_simulation(self):
        """测试解析失败模拟"""
        provider = FakeLLMProvider(simulate_parse_failure=True)
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            expected_schema_name="FusionRecommendation",
        )

        response = provider.generate(request)

        assert response.parse_success is False
        assert len(response.warnings) > 0

    def test_generate_timeout_simulation(self):
        """测试超时模拟"""
        provider = FakeLLMProvider(simulate_timeout=True)
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response = provider.generate(request)

        assert response.finish_reason == FinishReason.ERROR
        assert len(response.errors) > 0
        assert "timeout" in response.errors[0].code.lower()

    def test_generate_provider_error_simulation(self):
        """测试 Provider 错误模拟"""
        provider = FakeLLMProvider(simulate_error=True)
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(task_spec=task_spec, user_prompt="test")

        response = provider.generate(request)

        assert response.finish_reason == FinishReason.ERROR
        assert len(response.errors) > 0

    def test_generate_returns_valid_fusion_recommendation(self):
        """测试返回有效的融合建议结构"""
        provider = FakeLLMProvider()
        task_spec = LLMTaskSpec(
            task_type=TaskType.FUSION_RECOMMENDATION,
            need_structured_output=True,
        )
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            expected_schema_name="FusionRecommendation",
        )

        response = provider.generate(request)

        assert response.structured_data is not None
        data = response.structured_data

        # 验证 FusionRecommendation 结构
        assert "summary" in data
        assert "decision" in data
        assert data["decision"] in ["yes", "no", "conditional_yes", "needs_more_information"]
        assert "reasoning" in data
        assert isinstance(data["reasoning"], list)
        assert "risks" in data
        assert isinstance(data["risks"], list)
        assert "missing_information" in data
        assert isinstance(data["missing_information"], list)
        assert "next_actions" in data
        assert isinstance(data["next_actions"], list)
        assert "confidence" in data
        assert 0 <= data["confidence"] <= 1

    def test_generate_respects_max_tokens(self):
        """测试尊重 max_tokens 设置"""
        provider = FakeLLMProvider()
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
            max_tokens=100,
        )

        response = provider.generate(request)

        # fake provider 不实际截断，但应该有 usage 信息
        assert response.usage is not None
        assert response.usage.output_tokens > 0

    def test_multiple_requests_cycle_responses(self):
        """测试多个请求循环返回自定义响应"""
        responses = [
            {"raw_text": "Response 1"},
            {"raw_text": "Response 2"},
            {"raw_text": "Response 3"},
        ]

        provider = FakeLLMProvider(responses=responses)
        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)

        for i in range(6):
            request = LLMRequest(task_spec=task_spec, user_prompt="test")
            response = provider.generate(request)
            expected_idx = i % 3
            assert response.raw_text == f"Response {expected_idx + 1}"