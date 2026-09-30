"""
AnthropicCompatibleProvider

LLM Gateway / Provider Layer

Anthropic-compatible LLM Provider，支持 Anthropic Messages API 格式的 HTTP 端点。

配置支持：
- LLM_BASE_URL (优先) 或 ANTHROPIC_BASE_URL (回退)
- LLM_AUTH_TOKEN (优先) 或 ANTHROPIC_AUTH_TOKEN (回退)
- LLM_*_MODEL 系列环境变量定义物理模型名称

安全：
- Token 只在 HTTP header 中传递
- 错误信息中不暴露 token
- 日志中不记录 token
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from src.domain.services.llm_provider import LLMProvider
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_response import (
    LLMResponse,
    LLMUsage,
    LLMError,
    FinishReason,
)
from src.infra.llm.config.llm_settings import LLMSettings

logger = logging.getLogger(__name__)


class AnthropicProviderError(Exception):
    """Anthropic Provider 错误基类"""

    def __init__(self, code: str, message: str, details: Optional[list[str]] = None):
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(f"[{code}] {message}")


class AnthropicAuthError(AnthropicProviderError):
    """认证错误"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__("AUTH_ERROR", message)


class AnthropicProviderTimeout(AnthropicProviderError):
    """超时错误"""

    def __init__(self, timeout_ms: int):
        super().__init__(
            "TIMEOUT_ERROR",
            f"Request timed out after {timeout_ms}ms. Check LLM_BASE_URL connectivity."
        )


class AnthropicCompatibleProvider(LLMProvider):
    """
    Anthropic-compatible LLM Provider

    支持 Anthropic Messages API 格式的 HTTP 端点。

    配置：
        base_url: API 基础 URL（从 LLM_BASE_URL 或 ANTHROPIC_BASE_URL 读取）
        auth_token: 认证 token（从 LLM_AUTH_TOKEN 或 ANTHROPIC_AUTH_TOKEN 读取）
        timeout_ms: 请求超时（毫秒）

    环境变量优先级：
        LLM_* 优先于 ANTHROPIC_*
    """

    def __init__(
        self,
        settings: Optional[LLMSettings] = None,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ):
        """
        初始化 Provider

        Args:
            settings: LLM 配置（优先使用）
            base_url: API 基础 URL（可选，覆盖 settings）
            auth_token: 认证 token（可选，覆盖 settings）
            timeout_ms: 超时时间（可选，覆盖 settings）

        Raises:
            AnthropicProviderError: 缺少必要配置
        """
        # 加载配置
        if settings is None:
            settings = LLMSettings()

        # 读取 base_url（支持回退）
        self._base_url = base_url or settings.base_url
        if not self._base_url:
            self._base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")

        if not self._base_url:
            raise AnthropicProviderError(
                "MISSING_CONFIG",
                "LLM_BASE_URL is required. Set LLM_BASE_URL environment variable."
            )

        # 读取 auth_token（支持回退）
        self._auth_token = auth_token or settings.auth_token
        if not self._auth_token:
            self._auth_token = os.environ.get("LLM_AUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN")

        # 清理token中的空白字符（防止复制粘贴错误）
        if self._auth_token:
            self._auth_token = self._auth_token.strip()

        if not self._auth_token:
            raise AnthropicProviderError(
                "MISSING_CONFIG",
                "LLM_AUTH_TOKEN is required. Set LLM_AUTH_TOKEN environment variable."
            )

        # 超时配置
        self._timeout_ms = timeout_ms or settings.default_timeout_ms

        # HTTP 客户端（延迟创建）
        self._client: Optional[httpx.Client] = None

    @property
    def base_url(self) -> str:
        """获取 base_url"""
        return self._base_url

    @property
    def auth_token(self) -> str:
        """获取 auth_token"""
        return self._auth_token

    def _get_client(self) -> httpx.Client:
        """获取或创建 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self._timeout_ms / 1000.0),
            )
        return self._client

    def generate(
        self,
        request: LLMRequest,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """
        执行 LLM 生成请求

        Args:
            request: LLM 请求对象
            model: 物理模型名称（可选，覆盖路由）

        Returns:
            LLMResponse: LLM 响应对象

        Raises:
            AnthropicAuthError: 认证失败
            AnthropicProviderTimeout: 请求超时
            AnthropicProviderError: 其他错误
        """
        start_time = time.time()

        # 确定使用的模型
        physical_model = model or "default-model"

        # 记录调用开始（用于 strict mode）
        from src.infra.observability.strict_mode_checker import get_strict_mode_checker
        checker = get_strict_mode_checker()
        # 我们无法从 request 对象直接确定 component（G1/G2/G5），由调用方负责记录
        # 这里我们只记录 raw LLM call

        # 构建请求体
        request_body = self._build_request_body(request, physical_model)

        # 构建请求 URL
        url = f"{self._base_url.rstrip('/')}/v1/messages"

        # 构建请求头
        headers = self._build_headers()

        # 调试日志：打印认证信息（不暴露完整token）
        token_preview = self._auth_token[:8] + "..." + self._auth_token[-4:] if len(self._auth_token) > 12 else "***"
        logger.info(
            f"[Anthropic] Sending request to {url}, "
            f"model={physical_model}, token_preview={token_preview}, "
            f"headers_keys={list(headers.keys())}"
        )

        try:
            client = self._get_client()
            response = client.post(
                url,
                json=request_body,
                headers=headers,
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # 检查响应状态
            if response.status_code != 200:
                self._handle_http_error(response, latency_ms)

            # 解析响应
            response_data = response.json()
            llm_response = self._parse_response(response_data, latency_ms)

            # 记录真实调用
            from src.infra.observability.service_counters import get_service_counters
            counters = get_service_counters()
            counters.increment_llm_real_call()

            # 记录配置信息
            logger.info(
                f"LLM API call succeeded: "
                f"base_url={self._base_url}, "
                f"model={physical_model}, "
                f"latency={latency_ms}ms, "
                f"tokens={llm_response.usage.total_tokens if llm_response.usage else 0}"
            )

            return llm_response

        except httpx.TimeoutException:
            latency_ms = int((time.time() - start_time) * 1000)

            # 记录错误
            from src.infra.observability.service_counters import get_service_counters
            from src.infra.observability.fallback_logger import get_fallback_logger
            counters = get_service_counters()
            counters.increment_llm_error()

            fallback_logger = get_fallback_logger()
            fallback_logger.log_fallback(
                fallback_type="llm_unavailable",
                reason=f"Timeout after {latency_ms}ms",
                affected_component="anthropic_provider",
                severity="error",
            )

            self._handle_timeout_error(latency_ms)

        except httpx.RequestError as e:
            latency_ms = int((time.time() - start_time) * 1000)

            # 记录错误
            from src.infra.observability.service_counters import get_service_counters
            from src.infra.observability.fallback_logger import get_fallback_logger
            counters = get_service_counters()
            counters.increment_llm_error()

            fallback_logger = get_fallback_logger()
            fallback_logger.log_fallback(
                fallback_type="llm_unavailable",
                reason=f"Network error: {str(e)}",
                affected_component="anthropic_provider",
                severity="error",
            )

            raise AnthropicProviderError(
                "NETWORK_ERROR",
                f"Network error: {str(e)}. Check LLM_BASE_URL connectivity."
            )

    def _build_headers(self) -> dict[str, str]:
        """
        构建 HTTP 请求头

        支持多种认证方式（兼容不同API提供商）：
        1. x-api-key（Anthropic原生格式）
        2. Authorization: Bearer（OpenAI兼容格式）

        部分 Anthropic 兼容端点同时支持两种认证方式

        ⚠️ 重要警告 ⚠️
        ================
        不要删除以下任一认证header！某些 LLM 端点需要根据请求类型
        使用不同的认证方式验证。删除任何一个都会导致 HTTP 401 错误。

        历史问题记录：
        - 2026-04-03: 只使用 x-api-key 导致 401，已修复
        - 根因: 部分 LLM 端点需要同时支持两种认证格式

        文档参考：
        - docs/KNOWN_ISSUES.md 第 1.2 节
        - docs/CHANGELOG.md 2026-04-03 更新

        Returns:
            请求头字典
        """
        return {
            "Content-Type": "application/json",
            "x-api-key": self._auth_token,
            "Authorization": f"Bearer {self._auth_token}",  # ⚠️ 必须保留！删除会导致401
            "anthropic-version": "2023-06-01",
        }

    def _build_request_body(
        self,
        request: LLMRequest,
        model: str,
    ) -> dict:
        """
        构建 Anthropic Messages API 请求体

        Args:
            request: LLM 请求对象
            model: 物理模型名称

        Returns:
            请求体字典
        """
        body: dict = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": request.user_prompt,
                }
            ],
        }

        # 添加 system prompt
        if request.system_prompt:
            body["system"] = request.system_prompt

        # 添加 temperature
        if request.temperature is not None:
            body["temperature"] = request.temperature

        # GLM 模型禁用 thinking 模式，直接返回最终答案
        # 对于支持 extended thinking 的模型，需要显式禁用以获得直接输出
        if "glm" in model.lower() or "GLM" in model:
            body["thinking"] = {"type": "disabled"}
            logger.debug(f"[Anthropic] Disabled thinking mode for GLM model: {model}")

        return body

    def _parse_response(
        self,
        response_data: dict,
        latency_ms: int,
    ) -> LLMResponse:
        """
        解析 Anthropic 响应

        Args:
            response_data: 响应数据
            latency_ms: 延迟（毫秒）

        Returns:
            LLMResponse 对象
        """
        # 提取文本内容
        content = response_data.get("content", [])
        raw_text = ""
        thinking_text = ""
        has_thinking = False
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                raw_text += block.get("text", "")
            elif block_type == "thinking":
                # GLM 等模型的 extended thinking 块
                # 某些模型（如 GLM）可能只返回 thinking 块，此时也作为有效输出
                has_thinking = True
                thinking_text += block.get("thinking", "") or block.get("text", "")

        # 如果没有 text 块但有 thinking 块，使用 thinking 内容
        # 这处理 GLM 等模型只返回 thinking 的情况
        if not raw_text and thinking_text:
            raw_text = thinking_text
            logger.debug(f"[Anthropic] Using thinking block as output, len={len(raw_text)}")

        # 解析 stop_reason
        stop_reason = response_data.get("stop_reason", "end_turn")
        finish_reason = self._map_stop_reason(stop_reason)

        # 解析 usage
        usage_data = response_data.get("usage", {})
        usage = LLMUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        )

        # 模型 ID
        model_id = response_data.get("model", "unknown")

        # 警告
        warnings: list[str] = []
        if not raw_text:
            warnings.append("Empty response content")

        return LLMResponse(
            provider_id="anthropic",
            model_id=model_id,
            raw_text=raw_text,
            structured_data=None,  # 结构化解析由 Gateway 层处理
            parse_success=True,
            latency_ms=latency_ms,
            usage=usage,
            finish_reason=finish_reason,
            warnings=warnings,
        )

    def _map_stop_reason(self, stop_reason: str) -> FinishReason:
        """
        映射 stop_reason 到 FinishReason

        Args:
            stop_reason: Anthropic stop_reason

        Returns:
            FinishReason 枚举值
        """
        mapping = {
            "end_turn": FinishReason.STOP,
            "stop_sequence": FinishReason.STOP,
            "max_tokens": FinishReason.LENGTH,
        }
        return mapping.get(stop_reason, FinishReason.STOP)

    def _handle_http_error(self, response: httpx.Response, latency_ms: int) -> None:
        """
        处理 HTTP 错误

        Args:
            response: HTTP 响应
            latency_ms: 延迟（毫秒）

        Raises:
            AnthropicAuthError: 认证错误
            AnthropicProviderError: 其他错误
        """
        status_code = response.status_code

        # 认证错误
        if status_code in (401, 403):
            raise AnthropicAuthError(
                f"Authentication failed (HTTP {status_code}). Check LLM_AUTH_TOKEN."
            )

        # 客户端错误
        if 400 <= status_code < 500:
            try:
                error_body = response.json()
                error_msg = error_body.get("error", {}).get("message", response.text)
            except Exception:
                error_msg = response.text[:200]

            raise AnthropicProviderError(
                f"CLIENT_ERROR_{status_code}",
                f"Request failed (HTTP {status_code}): {error_msg}"
            )

        # 服务端错误
        if status_code >= 500:
            raise AnthropicProviderError(
                "PROVIDER_ERROR",
                f"Provider error (HTTP {status_code}). Try again later."
            )

        # 其他错误
        raise AnthropicProviderError(
            "UNKNOWN_ERROR",
            f"Unexpected HTTP status: {status_code}"
        )

    def _handle_timeout_error(self, latency_ms: int) -> None:
        """
        处理超时错误

        Args:
            latency_ms: 延迟（毫秒）

        Raises:
            AnthropicProviderTimeout: 超时错误
        """
        raise AnthropicProviderTimeout(self._timeout_ms)

    def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "AnthropicCompatibleProvider":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.close()


__all__ = [
    "AnthropicCompatibleProvider",
    "AnthropicProviderError",
    "AnthropicAuthError",
    "AnthropicProviderTimeout",
]