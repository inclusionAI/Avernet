"""
AnthropicCompatibleProvider 单元测试

测试 Anthropic-compatible provider 的配置读取、请求构建、响应处理和错误处理。
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
import httpx

from src.infra.llm.providers.anthropic_compatible_provider import (
    AnthropicCompatibleProvider,
    AnthropicProviderError,
    AnthropicAuthError,
    AnthropicProviderTimeout,
)
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
from src.domain.models.llm_response import FinishReason


class TestAnthropicCompatibleProviderInit:
    """Provider 初始化测试"""

    def test_init_with_settings(self):
        """测试使用 settings 初始化"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        assert provider is not None
        assert provider.base_url == "https://api.example.com"
        assert provider.auth_token == "test-token-placeholder"

    def test_init_with_env_variables(self):
        """测试从环境变量初始化"""
        env = {
            "LLM_BASE_URL": "https://llm.example.com",
            "LLM_AUTH_TOKEN": "env-token-placeholder",
        }

        with patch.dict(os.environ, env, clear=True):
            provider = AnthropicCompatibleProvider()

            assert provider.base_url == "https://llm.example.com"
            assert provider.auth_token == "env-token-placeholder"

    def test_init_fallback_to_anthropic_env(self):
        """测试回退到 ANTHROPIC_* 环境变量"""
        env = {
            "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
            "ANTHROPIC_AUTH_TOKEN": "anthropic-token-placeholder",
        }

        with patch.dict(os.environ, env, clear=True):
            provider = AnthropicCompatibleProvider()

            assert provider.base_url == "https://anthropic.example.com"
            assert provider.auth_token == "anthropic-token-placeholder"

    def test_init_llm_env_takes_priority(self):
        """测试 LLM_* 环境变量优先于 ANTHROPIC_*"""
        env = {
            "LLM_BASE_URL": "https://llm.example.com",
            "LLM_AUTH_TOKEN": "llm-token-placeholder",
            "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
            "ANTHROPIC_AUTH_TOKEN": "anthropic-token-placeholder",
        }

        with patch.dict(os.environ, env, clear=True):
            provider = AnthropicCompatibleProvider()

            # LLM_* 应该优先
            assert provider.base_url == "https://llm.example.com"
            assert provider.auth_token == "llm-token-placeholder"

    def test_init_missing_base_url(self):
        """测试缺少 base_url 时报错"""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(AnthropicProviderError) as exc_info:
                AnthropicCompatibleProvider()

            assert "LLM_BASE_URL" in str(exc_info.value)

    def test_init_missing_auth_token(self):
        """测试缺少 auth_token 时报错"""
        env = {"LLM_BASE_URL": "https://api.example.com"}

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(AnthropicProviderError) as exc_info:
                AnthropicCompatibleProvider()

            assert "LLM_AUTH_TOKEN" in str(exc_info.value)


class TestAnthropicCompatibleProviderRequest:
    """请求构建测试"""

    def test_build_request_body_basic(self):
        """测试构建基本请求体"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="Hello, world!",
        )

        body = provider._build_request_body(request, "test-model")

        assert body["model"] == "test-model"
        assert body["max_tokens"] == 4096  # default
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == "Hello, world!"

    def test_build_request_body_with_system_prompt(self):
        """测试构建带 system prompt 的请求体"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            system_prompt="You are a helpful assistant.",
            user_prompt="Hello!",
        )

        body = provider._build_request_body(request, "test-model")

        assert body["system"] == "You are a helpful assistant."
        assert body["messages"][0]["content"] == "Hello!"

    def test_build_request_body_with_custom_params(self):
        """测试构建带自定义参数的请求体"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="Hello!",
            max_tokens=4096,
            temperature=0.5,
        )

        body = provider._build_request_body(request, "test-model")

        assert body["max_tokens"] == 4096
        assert body["temperature"] == 0.5


class TestAnthropicCompatibleProviderResponse:
    """响应处理测试"""

    def test_parse_success_response(self):
        """测试解析成功响应"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        response_data = {
            "id": "msg-123",
            "model": "claude-3-opus",
            "content": [
                {"type": "text", "text": "Hello! How can I help you?"}
            ],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
            }
        }

        parsed = provider._parse_response(response_data, 100)

        assert parsed.provider_id == "anthropic"
        assert parsed.model_id == "claude-3-opus"
        assert parsed.raw_text == "Hello! How can I help you?"
        assert parsed.finish_reason == FinishReason.STOP
        assert parsed.latency_ms == 100
        assert parsed.usage.input_tokens == 10
        assert parsed.usage.output_tokens == 20
        assert parsed.parse_success is True

    def test_parse_response_with_stop_reason_length(self):
        """测试解析 length stop_reason"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        response_data = {
            "id": "msg-123",
            "model": "test-model",
            "content": [{"type": "text", "text": "Truncated..."}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 10, "output_tokens": 100}
        }

        parsed = provider._parse_response(response_data, 50)

        assert parsed.finish_reason == FinishReason.LENGTH

    def test_parse_empty_response(self):
        """测试解析空响应"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        response_data = {
            "id": "msg-123",
            "model": "test-model",
            "content": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 0}
        }

        parsed = provider._parse_response(response_data, 50)

        assert parsed.raw_text == ""
        assert len(parsed.warnings) > 0


class TestAnthropicCompatibleProviderErrors:
    """错误处理测试"""

    def test_handle_401_error(self):
        """测试处理 401 认证错误"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = '{"error": "Unauthorized"}'

        with pytest.raises(AnthropicAuthError) as exc_info:
            provider._handle_http_error(mock_response, 0)

        # 错误信息不应包含 token
        assert "test-token-placeholder" not in str(exc_info.value)
        assert "LLM_AUTH_TOKEN" in str(exc_info.value)

    def test_handle_403_error(self):
        """测试处理 403 权限错误"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.text = '{"error": "Forbidden"}'

        with pytest.raises(AnthropicAuthError):
            provider._handle_http_error(mock_response, 0)

    def test_handle_4xx_error(self):
        """测试处理 4xx 客户端错误"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = '{"error": "Bad request"}'

        with pytest.raises(AnthropicProviderError) as exc_info:
            provider._handle_http_error(mock_response, 0)

        assert "CLIENT_ERROR" in exc_info.value.code

    def test_handle_5xx_error(self):
        """测试处理 5xx 服务端错误"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.text = '{"error": "Internal server error"}'

        with pytest.raises(AnthropicProviderError) as exc_info:
            provider._handle_http_error(mock_response, 0)

        assert exc_info.value.code == "PROVIDER_ERROR"

    def test_handle_timeout(self):
        """测试处理超时"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        with pytest.raises(AnthropicProviderTimeout) as exc_info:
            provider._handle_timeout_error(15000)

        assert "timeout" in str(exc_info.value).lower()
        # 不应包含 token
        assert "test-token-placeholder" not in str(exc_info.value)


class TestAnthropicCompatibleProviderTokenSafety:
    """Token 安全测试"""

    def test_token_not_in_error_message(self):
        """测试 token 不出现在错误信息中"""
        from src.infra.llm.config.llm_settings import LLMSettings

        secret_token = "sk-secret-token-12345"
        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token=secret_token,
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        # 模拟各种错误，确保 token 不泄露
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.text = f'{{"error": "Invalid token: {secret_token}"}}'

        try:
            provider._handle_http_error(mock_response, 0)
        except AnthropicAuthError as e:
            error_str = str(e)
            assert secret_token not in error_str
            # 即使响应中有 token，错误信息也不应包含

    def test_token_only_in_header(self):
        """测试 token 只在 header 中"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
        )

        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "msg-123",
                "model": "test-model",
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5}
            }
            mock_request.return_value = mock_response

            provider.generate(request, "test-model")

            # 检查调用参数
            call_args = mock_request.call_args
            headers = call_args.kwargs.get('headers', {})

            # token 应该只在 Authorization header 中
            assert "x-api-key" in headers or "authorization" in {k.lower() for k in headers.keys()}

            # body 中不应有 token
            body = call_args.kwargs.get('json', {})
            assert "token" not in str(body).lower() or "test-token-placeholder" not in str(body)

    def test_dual_auth_headers_for_compatible_endpoint(self):
        """
        Test that both x-api-key and Authorization: Bearer headers are sent.

        Some Anthropic-compatible endpoints require dual auth headers.
        The provider automatically sends both to ensure compatibility.
        """
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://mock-llm-endpoint.example.com/api/anthropic",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        # 获取 headers
        headers = provider._build_headers()

        # 必须同时包含两个认证 header
        assert "x-api-key" in headers, "缺少 x-api-key header（Anthropic格式）"
        assert "Authorization" in headers, "缺少 Authorization header（OpenAI兼容格式）"
        assert headers["x-api-key"] == "test-token-placeholder"
        assert headers["Authorization"] == "Bearer test-token-placeholder"

        # 同时验证其他必需 header
        assert headers["Content-Type"] == "application/json"
        assert headers["anthropic-version"] == "2023-06-01"


class TestAnthropicCompatibleProviderGenerate:
    """generate 方法完整测试"""

    def test_generate_success(self):
        """测试成功生成"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="Hello!",
        )

        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "msg-123",
                "model": "test-model",
                "content": [{"type": "text", "text": "Hello! How can I help?"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 10}
            }
            mock_request.return_value = mock_response

            response = provider.generate(request, "test-model")

            assert response.raw_text == "Hello! How can I help?"
            assert response.finish_reason == FinishReason.STOP
            assert response.parse_success is True

    def test_generate_with_custom_model(self):
        """测试使用自定义模型"""
        from src.infra.llm.config.llm_settings import LLMSettings

        settings = LLMSettings(
            base_url="https://api.example.com",
            auth_token="test-token-placeholder",
        )
        provider = AnthropicCompatibleProvider(settings=settings)

        task_spec = LLMTaskSpec(task_type=TaskType.SUMMARY)
        request = LLMRequest(
            task_spec=task_spec,
            user_prompt="test",
        )

        with patch('httpx.Client.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "id": "msg-123",
                "model": "GLM-5",
                "content": [{"type": "text", "text": "response"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5}
            }
            mock_request.return_value = mock_response

            response = provider.generate(request, "GLM-5")

            # 验证请求体使用了正确的模型
            call_args = mock_request.call_args
            body = call_args.kwargs.get('json', {})
            assert body["model"] == "GLM-5"
            assert response.model_id == "GLM-5"