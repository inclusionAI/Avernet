"""
LLMResponse 领域模型测试

测试 LLM 响应模型的验证和行为。
"""

import pytest
from pydantic import ValidationError

from src.domain.models.llm_response import (
    LLMResponse,
    LLMUsage,
    FinishReason,
    LLMError,
)


class TestLLMUsage:
    """LLMUsage 测试"""

    def test_create_usage(self):
        """测试创建使用量"""
        usage = LLMUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )

        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150

    def test_usage_non_negative(self):
        """测试 token 数必须非负"""
        with pytest.raises(ValidationError):
            LLMUsage(input_tokens=-1, output_tokens=0, total_tokens=0)

        with pytest.raises(ValidationError):
            LLMUsage(input_tokens=0, output_tokens=-1, total_tokens=0)


class TestFinishReason:
    """FinishReason 枚举测试"""

    def test_finish_reason_values(self):
        """测试结束原因枚举值"""
        assert FinishReason.STOP == "stop"
        assert FinishReason.LENGTH == "length"
        assert FinishReason.CONTENT_FILTER == "content_filter"
        assert FinishReason.ERROR == "error"
        assert FinishReason.UNKNOWN == "unknown"


class TestLLMError:
    """LLMError 测试"""

    def test_create_error(self):
        """测试创建错误"""
        error = LLMError(
            code="PROVIDER_ERROR",
            message="Connection timeout",
        )

        assert error.code == "PROVIDER_ERROR"
        assert error.message == "Connection timeout"
        assert error.details == []

    def test_error_with_details(self):
        """测试带详情的错误"""
        error = LLMError(
            code="PARSE_ERROR",
            message="Failed to parse JSON",
            details=["Invalid JSON at line 1", "Expected '}'"],
        )

        assert error.code == "PARSE_ERROR"
        assert error.message == "Failed to parse JSON"
        assert error.details == ["Invalid JSON at line 1", "Expected '}'"]


class TestLLMResponse:
    """LLMResponse 模型测试"""

    def test_create_success_response(self):
        """测试创建成功响应"""
        usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150)

        response = LLMResponse(
            provider_id="anthropic",
            model_id="claude-3-opus",
            raw_text="这是一个测试响应。",
            structured_data={"decision": "yes"},
            parse_success=True,
            latency_ms=500,
            usage=usage,
            finish_reason=FinishReason.STOP,
        )

        assert response.provider_id == "anthropic"
        assert response.model_id == "claude-3-opus"
        assert response.raw_text == "这是一个测试响应。"
        assert response.structured_data == {"decision": "yes"}
        assert response.parse_success is True
        assert response.latency_ms == 500
        assert response.usage.input_tokens == 100
        assert response.finish_reason == FinishReason.STOP
        assert response.warnings == []
        assert response.errors == []

    def test_create_error_response(self):
        """测试创建错误响应"""
        error = LLMError(code="TIMEOUT", message="Request timed out")

        response = LLMResponse(
            provider_id="anthropic",
            model_id="claude-3-opus",
            raw_text="",
            parse_success=False,
            latency_ms=15000,
            finish_reason=FinishReason.ERROR,
            errors=[error],
        )

        assert response.parse_success is False
        assert response.finish_reason == FinishReason.ERROR
        assert len(response.errors) == 1
        assert response.errors[0].code == "TIMEOUT"

    def test_create_parse_failure_response(self):
        """测试创建解析失败响应"""
        response = LLMResponse(
            provider_id="anthropic",
            model_id="claude-3-opus",
            raw_text="This is not valid JSON",
            structured_data=None,
            parse_success=False,
            latency_ms=500,
            finish_reason=FinishReason.STOP,
            warnings=["Failed to parse structured output"],
        )

        assert response.parse_success is False
        assert response.structured_data is None
        assert "Failed to parse structured output" in response.warnings

    def test_required_fields(self):
        """测试必填字段"""
        with pytest.raises(ValidationError) as exc_info:
            LLMResponse()

        errors = exc_info.value.errors()
        error_fields = {e["loc"][0] for e in errors}
        assert "provider_id" in error_fields
        assert "model_id" in error_fields
        # raw_text has default value "", not required
        assert "parse_success" in error_fields
        assert "latency_ms" in error_fields
        assert "finish_reason" in error_fields

    def test_latency_non_negative(self):
        """测试延迟必须非负"""
        with pytest.raises(ValidationError):
            LLMResponse(
                provider_id="test",
                model_id="test",
                raw_text="test",
                parse_success=True,
                latency_ms=-1,
                finish_reason=FinishReason.STOP,
            )

    def test_model_dump(self):
        """测试模型序列化"""
        response = LLMResponse(
            provider_id="anthropic",
            model_id="claude-3-opus",
            raw_text="test",
            structured_data={"key": "value"},
            parse_success=True,
            latency_ms=500,
            finish_reason=FinishReason.STOP,
        )

        data = response.model_dump()

        assert data["provider_id"] == "anthropic"
        assert data["model_id"] == "claude-3-opus"
        assert data["raw_text"] == "test"
        assert data["structured_data"] == {"key": "value"}
        assert data["parse_success"] is True
        assert data["latency_ms"] == 500
        assert data["finish_reason"] == "stop"