"""
OpenAICompatibleProvider

LLM Gateway / Provider Layer

OpenAI-compatible LLM Provider，支持 OpenAI Chat Completions API 格式的 HTTP 端点。

配置支持：
- LLM_BASE_URL (优先)
- LLM_AUTH_TOKEN (优先)
- LLM_*_MODEL 系列环境变量定义物理模型名称
- LLM_STREAM 启用流式调用（默认 false，singlebox 中默认开启）

安全：
- Token 只在 HTTP header 中传递
- 错误信息中不暴露 token
- 日志中不记录 token
"""

from __future__ import annotations

import json
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


class OpenAIProviderError(Exception):
    """OpenAI Provider 错误基类"""

    def __init__(self, code: str, message: str, details: Optional[list[str]] = None):
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(f"[{code}] {message}")


class OpenAIAuthError(OpenAIProviderError):
    """认证错误"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__("AUTH_ERROR", message)


class OpenAIProviderTimeout(OpenAIProviderError):
    """超时错误"""

    def __init__(self, timeout_ms: int):
        super().__init__(
            "TIMEOUT_ERROR",
            f"Request timed out after {timeout_ms}ms. Check LLM_BASE_URL connectivity."
        )


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI-compatible LLM Provider

    支持 OpenAI Chat Completions API 格式的 HTTP 端点。
    新增流式调用能力：当 LLM_STREAM 开启时，使用 SSE 流式读取响应，
    避免非流式长生成被中间网关（如 antchat 90s 读超时）切断。

    配置：
        base_url: API 基础 URL（通常已包含 /v1，例如 https://api.openai.com/v1）
        auth_token: 认证 token（从 LLM_AUTH_TOKEN 读取）
        timeout_ms: 非流式请求超时（毫秒）
    """

    def __init__(
        self,
        settings: Optional[LLMSettings] = None,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ):
        if settings is None:
            settings = LLMSettings()

        self._base_url = base_url or settings.base_url
        if not self._base_url:
            self._base_url = os.environ.get("LLM_BASE_URL")

        if not self._base_url:
            raise OpenAIProviderError(
                "MISSING_CONFIG",
                "LLM_BASE_URL is required. Set LLM_BASE_URL environment variable."
            )

        self._auth_token = auth_token or settings.auth_token
        if not self._auth_token:
            self._auth_token = os.environ.get("LLM_AUTH_TOKEN")

        if self._auth_token:
            self._auth_token = self._auth_token.strip()

        if not self._auth_token:
            raise OpenAIProviderError(
                "MISSING_CONFIG",
                "LLM_AUTH_TOKEN is required. Set LLM_AUTH_TOKEN environment variable."
            )

        self._timeout_ms = timeout_ms or settings.default_timeout_ms
        self._client: Optional[httpx.Client] = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def auth_token(self) -> str:
        return self._auth_token

    def _use_stream(self) -> bool:
        """Return True if streaming mode is enabled via LLM_STREAM env var."""
        stream = os.environ.get("LLM_STREAM", "").lower()
        return stream in ("1", "true", "yes", "on")

    def _build_client_timeout(self) -> httpx.Timeout:
        """Build httpx timeout for the current mode."""
        if self._use_stream():
            # Streaming: no total timeout; keep each token gap under the antchat
            # 90s gateway window while allowing long overall generations.
            return httpx.Timeout(None, connect=10.0, read=90.0, write=30.0)
        return httpx.Timeout(self._timeout_ms / 1000.0)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._build_client_timeout())
        return self._client

    def generate(
        self,
        request: LLMRequest,
        model: Optional[str] = None,
    ) -> LLMResponse:
        start_time = time.time()
        physical_model = model or "default-model"

        url = f"{self._base_url.rstrip('/')}/chat/completions"
        headers = self._build_headers()

        token_preview = self._auth_token[:8] + "..." + self._auth_token[-4:] if len(self._auth_token) > 12 else "***"
        logger.info(
            f"[OpenAI] Sending {'streaming' if self._use_stream() else 'sync'} request to {url}, "
            f"model={physical_model}, token_preview={token_preview}, "
            f"headers_keys={list(headers.keys())}"
        )

        if self._use_stream():
            return self._generate_streaming(request, physical_model, url, headers, start_time)
        return self._generate_sync(request, physical_model, url, headers, start_time)

    def _generate_sync(
        self,
        request: LLMRequest,
        model: str,
        url: str,
        headers: dict[str, str],
        start_time: float,
    ) -> LLMResponse:
        request_body = self._build_request_body(request, model)

        try:
            client = self._get_client()
            response = client.post(
                url,
                json=request_body,
                headers=headers,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code != 200:
                self._handle_http_error(response, latency_ms)

            response_data = response.json()
            llm_response = self._parse_response(response_data, latency_ms)

            from src.infra.observability.service_counters import get_service_counters
            counters = get_service_counters()
            counters.increment_llm_real_call()

            logger.info(
                f"LLM API call succeeded: "
                f"base_url={self._base_url}, "
                f"model={model}, "
                f"latency={latency_ms}ms, "
                f"tokens={llm_response.usage.total_tokens if llm_response.usage else 0}"
            )

            return llm_response

        except httpx.TimeoutException:
            latency_ms = int((time.time() - start_time) * 1000)
            self._record_llm_error("Timeout after {latency_ms}ms")
            self._handle_timeout_error(latency_ms)

        except httpx.RequestError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self._record_llm_error(f"Network error: {str(e)}")
            raise OpenAIProviderError(
                "NETWORK_ERROR",
                f"Network error: {str(e)}. Check LLM_BASE_URL connectivity."
            )

    def _generate_streaming(
        self,
        request: LLMRequest,
        model: str,
        url: str,
        headers: dict[str, str],
        start_time: float,
    ) -> LLMResponse:
        """Stream chat completions via SSE.

        Mirrors the backend harness streaming approach in #922:
        - POST with stream=True
        - Read data: lines and accumulate delta.content
        - Keep connection alive so antchat's ~90s gateway window is measured
          between tokens, not over the whole response.
        """
        request_body = self._build_request_body(request, model)
        request_body["stream"] = True

        raw_text = ""
        model_id = "unknown"
        finish_reason_str = "stop"
        usage_data: dict = {}
        saw_first_delta = False

        try:
            client = self._get_client()
            with client.stream(
                "POST",
                url,
                json=request_body,
                headers=headers,
            ) as response:
                response.raise_for_status()

                # Some endpoints ignore stream=True and return a normal JSON completion.
                # Detect that early and parse it as a regular response instead of SSE.
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    logger.warning(
                        "[OpenAI] Streaming requested but response Content-Type is %r; "
                        "falling back to non-stream parse.",
                        content_type,
                    )
                    fallback_data = json.loads(response.read())
                    latency_ms = int((time.time() - start_time) * 1000)
                    from src.infra.observability.service_counters import get_service_counters
                    counters = get_service_counters()
                    counters.increment_llm_real_call()
                    logger.info(
                        "[OpenAI] Non-stream fallback parse succeeded: latency=%dms",
                        latency_ms,
                    )
                    return self._parse_response(fallback_data, latency_ms)

                for line in response.iter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue

                    # SSE data lines: be tolerant of both "data: {" and "data:{" forms.
                    if not line.startswith("data:"):
                        continue

                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning("[OpenAI] Ignoring malformed SSE chunk: %s", data_str[:200])
                        continue

                    if chunk.get("model"):
                        model_id = chunk["model"]

                    chunk_usage = chunk.get("usage")
                    if chunk_usage:
                        usage_data = chunk_usage

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {}) or {}
                    # Some antchat/GLM models put reasoning in reasoning_content while
                    # leaving content empty. Treat empty content as absent and fall
                    # back to reasoning_content so reasoning-only streams still work.
                    delta_text = delta.get("content") or delta.get("reasoning_content")
                    if delta_text:
                        raw_text += delta_text

                    if not saw_first_delta:
                        saw_first_delta = True
                        logger.info(
                            "[OpenAI] First SSE delta keys=%s content_preview=%r",
                            list(delta.keys()),
                            delta_text,
                        )

                    choice_finish = choice.get("finish_reason")
                    if choice_finish:
                        finish_reason_str = choice_finish

        except httpx.TimeoutException:
            latency_ms = int((time.time() - start_time) * 1000)
            self._record_llm_error("Timeout after {latency_ms}ms")
            self._handle_timeout_error(latency_ms)

        except httpx.HTTPStatusError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            try:
                e.response.read()
            except Exception:
                pass
            self._handle_http_error(e.response, latency_ms)

        except httpx.RequestError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self._record_llm_error(f"Network error: {str(e)}")
            raise OpenAIProviderError(
                "NETWORK_ERROR",
                f"Network error: {str(e)}. Check LLM_BASE_URL connectivity."
            )

        latency_ms = int((time.time() - start_time) * 1000)

        if not raw_text:
            logger.warning(
                "[OpenAI] Streaming response finished with empty content "
                "(Content-Type was event-stream). finish_reason=%s",
                finish_reason_str,
            )

        from src.infra.observability.service_counters import get_service_counters
        counters = get_service_counters()
        counters.increment_llm_real_call()

        logger.info(
            f"LLM API streaming call succeeded: "
            f"base_url={self._base_url}, "
            f"model={model}, "
            f"latency={latency_ms}ms, "
            f"output_tokens={usage_data.get('completion_tokens', 0)}"
        )

        return self._build_response(
            raw_text=raw_text,
            model_id=model_id,
            latency_ms=latency_ms,
            usage_data=usage_data,
            finish_reason=finish_reason_str,
        )

    def _build_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._auth_token}",
        }

    def _build_request_body(
        self,
        request: LLMRequest,
        model: str,
    ) -> dict:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        # Allow operators to cap the maximum generation length via env var.
        # This prevents long outputs from exceeding the antchat 90s gateway
        # window in local singlebox deployments when streaming is disabled.
        max_tokens = request.max_tokens
        env_cap = os.environ.get("LLM_MAX_OUTPUT_TOKENS")
        if env_cap:
            try:
                max_tokens = min(max_tokens, int(env_cap))
            except ValueError:
                logger.warning("[OpenAI] Ignoring invalid LLM_MAX_OUTPUT_TOKENS: %s", env_cap)

        body: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        if request.temperature is not None:
            body["temperature"] = request.temperature

        return body

    def _parse_response(
        self,
        response_data: dict,
        latency_ms: int,
    ) -> LLMResponse:
        choices = response_data.get("choices", [])
        raw_text = ""
        finish_reason_str = "stop"
        if choices:
            choice = choices[0]
            message = choice.get("message", {}) or {}
            # Some antchat models (e.g. Kimi-K2.5) return reasoning in
            # reasoning_content while leaving content empty. Fall back so the
            # caller still gets usable text without changing the prompt.
            raw_text = message.get("content", "") or message.get("reasoning_content", "")
            finish_reason_str = choice.get("finish_reason", "stop")

        usage_data = response_data.get("usage", {})

        return self._build_response(
            raw_text=raw_text,
            model_id=response_data.get("model", "unknown"),
            latency_ms=latency_ms,
            usage_data=usage_data,
            finish_reason=finish_reason_str,
        )

    def _build_response(
        self,
        *,
        raw_text: str,
        model_id: str,
        latency_ms: int,
        usage_data: dict,
        finish_reason: str,
    ) -> LLMResponse:
        usage = LLMUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        warnings: list[str] = []
        if not raw_text:
            warnings.append("Empty response content")

        return LLMResponse(
            provider_id="openai",
            model_id=model_id,
            raw_text=raw_text,
            structured_data=None,
            parse_success=True,
            latency_ms=latency_ms,
            usage=usage,
            finish_reason=self._map_finish_reason(finish_reason),
            warnings=warnings,
            errors=[],
        )

    def _map_finish_reason(self, finish_reason: str) -> FinishReason:
        mapping = {
            "stop": FinishReason.STOP,
            "length": FinishReason.LENGTH,
            "content_filter": FinishReason.CONTENT_FILTER,
        }
        return mapping.get(finish_reason, FinishReason.UNKNOWN)

    def _handle_http_error(self, response: httpx.Response, latency_ms: int) -> None:
        status_code = response.status_code

        if status_code in (401, 403):
            raise OpenAIAuthError(
                f"Authentication failed (HTTP {status_code}). Check LLM_AUTH_TOKEN."
            )

        if 400 <= status_code < 500:
            try:
                error_body = response.json()
                error_msg = error_body.get("error", {}).get("message", response.text)
            except Exception:
                error_msg = response.text[:200]

            raise OpenAIProviderError(
                f"CLIENT_ERROR_{status_code}",
                f"Request failed (HTTP {status_code}): {error_msg}"
            )

        if status_code >= 500:
            raise OpenAIProviderError(
                "PROVIDER_ERROR",
                f"Provider error (HTTP {status_code}). Try again later."
            )

        raise OpenAIProviderError(
            "UNKNOWN_ERROR",
            f"Unexpected HTTP status: {status_code}"
        )

    def _handle_timeout_error(self, latency_ms: int) -> None:
        raise OpenAIProviderTimeout(self._timeout_ms)

    def _record_llm_error(self, reason: str) -> None:
        from src.infra.observability.service_counters import get_service_counters
        from src.infra.observability.fallback_logger import get_fallback_logger
        counters = get_service_counters()
        counters.increment_llm_error()
        fallback_logger = get_fallback_logger()
        fallback_logger.log_fallback(
            fallback_type="llm_unavailable",
            reason=reason,
            affected_component="openai_provider",
            severity="error",
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "OpenAICompatibleProvider":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


__all__ = [
    "OpenAICompatibleProvider",
    "OpenAIProviderError",
    "OpenAIAuthError",
    "OpenAIProviderTimeout",
]
