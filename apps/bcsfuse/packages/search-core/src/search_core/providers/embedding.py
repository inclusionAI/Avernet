"""
RealEmbeddingProvider

真实 Embedding Provider，调用外部 API 生成 embedding。

支持 OpenAI 兼容的 API 接口。
支持指数退避重试机制。
使用 httpx 作为 HTTP 客户端（与 LLM Provider 保持一致，避免 SSL 证书问题）。
"""

from __future__ import annotations

import logging
import math
import json
import time
from typing import Optional

import httpx

from search_core.plugin_api.embedding import EmbeddingProvider
from search_core.config.embedding import EmbeddingSettings
from search_core.providers.embedding_wire import (
    BINARY_CONTENT_TYPE,
    EMBEDDING_ACCEPT_HEADER,
    EmbeddingWireDimensionError,
    EmbeddingWireError,
    decode_embedding_vectors,
)

logger = logging.getLogger(__name__)


def _is_binary_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
    return content_type.strip().lower() == BINARY_CONTENT_TYPE


def _provider_reason_code(response: httpx.Response | None) -> str | None:
    if response is None or getattr(response, "status_code", None) != 429:
        return None
    try:
        payload: object = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    reason: object = error.get("param") or error.get("reason") or error.get("code")
    if isinstance(reason, str):
        try:
            decoded = json.loads(reason)
        except Exception:
            decoded = None
        if isinstance(decoded, dict):
            reason = decoded.get("errorCode")
    if isinstance(reason, dict):
        reason = reason.get("errorCode") or reason.get("reasonCode")
    if isinstance(reason, int):
        reason = str(reason)
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason.strip().upper()


class EmbeddingAPIError(Exception):
    """Embedding API 调用错误"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response: Optional[httpx.Response] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response
        self.provider_reason_code = _provider_reason_code(response)


class EmbeddingDimensionContractError(EmbeddingAPIError):
    """A successful embedding response violated the configured dimension contract."""

    def __init__(
        self,
        message: str,
        status_code: int | None = 415,
        response: httpx.Response | None = None,
    ):
        super().__init__(message, status_code=status_code, response=response)
        self.reason_code = "embedding_dimension_contract_mismatch"


class RealEmbeddingProvider(EmbeddingProvider):
    """
    Real Embedding Provider

    调用外部 Embedding API 生成向量。

    支持 OpenAI 兼容的 API 接口（如 OpenAI、Azure OpenAI、本地部署的 embedding 服务）。

    特点：
    - 调用真实 API
    - 支持批量请求
    - 支持超时配置
    - 自动错误处理
    - 支持指数退避重试（处理 Rate Limit）
    - 使用 httpx 客户端（与 LLM Provider 一致）

    Attributes:
        _settings: Embedding 配置
        _max_retries: 最大重试次数
        _base_delay_ms: 基础延迟时间（毫秒）
        _max_delay_ms: 最大延迟时间（毫秒）
    """

    def __init__(
        self,
        settings: EmbeddingSettings,
        max_retries: int = 4,
        base_delay_ms: int = 5000,
        max_delay_ms: int = 60000,
        request_interval_ms: int = 0,
        retry_budget_ms: int | None = None,
    ):
        """
        初始化 Real Provider

        Args:
            settings: 由应用提供的 Embedding 配置对象
            max_retries: 最大重试次数（默认 4 次，可从环境变量 EMBEDDING_MAX_RETRIES 配置）
            base_delay_ms: 基础延迟时间，毫秒（默认 5000ms = 5s，可从环境变量 EMBEDDING_RETRY_BACKOFF_SECONDS 配置）
            max_delay_ms: 最大延迟时间，毫秒（默认 60000ms = 60s）
            request_interval_ms: 兼容参数；内部请求间隔已移除，模型配额由外部限流器管理
            retry_budget_ms: 可选的重试总开销预算（毫秒）。不满足预算的重试会被跳过。
        """
        self._settings = settings
        self._max_retries = max_retries
        self._base_delay_ms = base_delay_ms
        self._max_delay_ms = max_delay_ms
        self._retry_budget_ms = retry_budget_ms
        # Keep zero permanently: providers must not serialize callers with sleep.
        self._request_interval_ms = 0
        self._client: Optional[httpx.Client] = None
        self._last_request_time: Optional[float] = None

        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if base_delay_ms <= 0:
            raise ValueError("base_delay_ms must be positive")
        if max_delay_ms <= 0:
            raise ValueError("max_delay_ms must be positive")
        if retry_budget_ms is not None and retry_budget_ms <= 0:
            raise ValueError("retry_budget_ms must be positive when configured")

        # 验证配置
        if not settings.is_configured():
            missing = settings.missing_config()
            raise ValueError(
                f"Embedding 配置不完整，缺失: {', '.join(missing)}。"
                f"请设置相应的环境变量或直接传入参数。"
            )

    def _get_client(self) -> httpx.Client:
        """获取或创建 httpx 客户端"""
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self._settings.timeout_ms / 1000.0),
            )
        return self._client

    def close(self) -> None:
        """关闭 httpx 客户端"""
        if self._client is not None:
            self._client.close()
            self._client = None

    def embed(self, text: str) -> list[float]:
        """
        生成文本的 embedding 向量

        Args:
            text: 输入文本

        Returns:
            list[float]: Embedding 向量

        Raises:
            EmbeddingAPIError: API 调用失败时抛出
        """
        results = self.embed_batch([text])
        return results[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量生成 embedding 向量

        支持指数退避重试，自动处理 Rate Limit (429) 和服务器错误 (5xx)。

        Args:
            texts: 输入文本列表

        Returns:
            list[list[float]]: Embedding 向量列表

        Raises:
            EmbeddingAPIError: API 调用失败时抛出
        """
        if not texts:
            return []

        # 记录开始时间
        start_time = time.monotonic()

        last_error = None

        for attempt in range(self._max_retries + 1):
            try:
                result = self._call_embedding_api(texts)

                # 计算延迟
                latency_ms = int((time.monotonic() - start_time) * 1000)

                # 记录真实调用
                self._record_success(latency_ms)

                # 记录配置信息
                logger.debug(
                    "Embedding API call succeeded: "
                    "model=%s, dimension=%d, latency=%dms, batch_size=%d",
                    self._settings.model,
                    self._settings.dimension,
                    latency_ms,
                    len(texts),
                )

                return result

            except EmbeddingAPIError as e:
                last_error = e

                # 记录最终失败；后续仍保持抛错语义。
                should_retry = self._should_retry(e.status_code, attempt)

                if not should_retry:
                    logger.error(
                        "EMBEDDING_PROVIDER_RETRY_FAILED: "
                        "attempts=%d status=%s model=%s provider=%s",
                        attempt + 1,
                        e.status_code,
                        self._settings.model,
                        self._settings.base_url,
                    )
                    self._record_failure(e)
                    raise

                response = getattr(e, "response", None)
                delay = self._calculate_delay(attempt, response)

                elapsed_ms = (time.monotonic() - start_time) * 1000
                retry_fits_budget = True
                if self._retry_budget_ms is not None:
                    retry_fits_budget = elapsed_ms + delay <= self._retry_budget_ms

                if not retry_fits_budget:
                    logger.error(
                        "EMBEDDING_RETRY_BUDGET_EXCEEDED: "
                        "attempts=%d elapsed_ms=%.1f delay_ms=%d budget_ms=%s "
                        "status=%s model=%s provider=%s",
                        attempt + 1,
                        elapsed_ms,
                        delay,
                        self._retry_budget_ms,
                        e.status_code,
                        self._settings.model,
                        self._settings.base_url,
                    )
                    self._record_failure(e)
                    raise

                # 如果是 429，记录更明确的日志
                if e.status_code == 429:
                    retry_after_seconds = delay / 1000.0
                    logger.warning(
                        f"Embedding API 调用失败 (attempt {attempt + 1}/{self._max_retries + 1}), "
                        f"status=429 RPM_LIMIT_EXCEEDED, waiting {retry_after_seconds:.1f}s before retry..."
                    )
                else:
                    logger.warning(
                        f"Embedding API 调用失败 (attempt {attempt + 1}/{self._max_retries + 1}), "
                        f"status={e.status_code}, waiting {delay / 1000.0:.1f}s before retry..."
                    )

                time.sleep(delay / 1000.0)

        # 不应该到达这里，但为了类型安全
        raise last_error or EmbeddingAPIError("未知错误")

    def _enforce_request_interval(self):
        """Compatibility no-op; model quotas are managed outside providers."""
        self._last_request_time = time.time()

    def _extract_retry_after(self, response: httpx.Response) -> Optional[int]:
        """
        从响应中提取 Retry-After header

        Args:
            response: HTTP 响应

        Returns:
            Optional[int]: Retry-After 秒数，如果没有则返回 None
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                # Retry-After 通常是秒数
                return int(retry_after)
            except ValueError:
                # 如果不是数字，忽略
                pass
        return None

    def _call_embedding_api(self, texts: list[str]) -> list[list[float]]:
        """
        实际调用 Embedding API

        使用 httpx 发送请求（与 LLM Provider 保持一致）。

        Args:
            texts: 输入文本列表

        Returns:
            list[list[float]]: Embedding 向量列表

        Raises:
            EmbeddingAPIError: API 调用失败时抛出
        """
        # 强制请求间隔
        self._enforce_request_interval()

        url = f"{self._settings.base_url}/embeddings"

        headers = {
            "Content-Type": "application/json",
            "Accept": EMBEDDING_ACCEPT_HEADER,
            "Authorization": f"Bearer {self._settings.auth_token}",
        }

        payload = {
            "input": texts,
            "model": self._settings.model,
        }
        if bool(getattr(self._settings, "request_dimensions", False)):
            # This is opt-in because some OpenAI-compatible model servers do
            # not implement the optional `dimensions` parameter. MRL-capable
            # servers use it to return a smaller vector without truncating it
            # in the application.
            payload["dimensions"] = self._settings.dimension

        # Log embedding request prepare (no secrets)
        from urllib.parse import urlparse

        try:
            base_url_host = (
                urlparse(self._settings.base_url).netloc
                or urlparse(self._settings.base_url).path.split("/")[0]
            )
        except Exception:
            base_url_host = "<invalid-url>"

        logger.info(
            "[EMBEDDING_REQUEST_PREPARE] provider_class=%s endpoint_path=%s model=%s dimension=%s text_count=%d timeout_ms=%d",
            self.__class__.__name__,
            "/embeddings",
            self._settings.model,
            self._settings.dimension,
            len(texts),
            self._settings.timeout_ms,
        )

        # 注意：不是所有 embedding 模型都支持 dimensions 参数
        # 只有支持 matryoshka representation 的模型才支持
        # 如果需要自定义维度，请确保模型支持
        # if self._settings.dimension:
        #     payload["dimensions"] = self._settings.dimension

        try:
            client = self._get_client()
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            if _is_binary_response(response):
                try:
                    vectors = decode_embedding_vectors(
                        response.content,
                        expected_count=len(texts),
                        dimension=self._settings.dimension,
                    )
                except EmbeddingWireError as e:
                    logger.error(
                        "[EMBEDDING_RESPONSE_PROTOCOL_ERROR] protocol=%s error=%s model=%s",
                        BINARY_CONTENT_TYPE,
                        e,
                        self._settings.model,
                    )
                    if isinstance(e, EmbeddingWireDimensionError):
                        self._record_dimension_contract_violation(
                            protocol="binary",
                            expected_dimension=e.expected_dimension,
                            actual_dimensions={e.actual_dimension},
                        )
                        raise EmbeddingDimensionContractError(
                            f"Embedding binary response dimension mismatch: expected "
                            f"{e.expected_dimension}, actual={e.actual_dimension}",
                            status_code=415,
                        ) from e
                    raise EmbeddingAPIError(
                        f"Embedding binary response is invalid: {e}",
                        status_code=415,
                    ) from e
                self._record_wire_protocol("binary")
                return vectors
            response_data = response.json()

        except httpx.HTTPStatusError as e:
            error_body = e.response.text

            # Log embedding request error
            logger.error(
                "[EMBEDDING_REQUEST_ERROR] status_code=%s error_type=%s message_preview=%s model=%s base_url_host=%s retry_count=%d",
                e.response.status_code,
                "HTTPStatusError",
                error_body[:200] if error_body else "",
                self._settings.model,
                base_url_host,
                0,  # Will be incremented in retry logic
            )

            # 提取 Retry-After header
            retry_after = self._extract_retry_after(e.response)

            # 429 Rate Limit - 特殊处理
            if e.response.status_code == 429:
                logger.error(f"Embedding API HTTP 错误: 429 - 请求额度超限(RPM)")
                if retry_after:
                    logger.warning(f"Retry-After header: {retry_after}秒")
                raise EmbeddingAPIError(
                    f"Embedding API 返回错误: 429 - 请求额度超限(RPM)",
                    status_code=429,
                    response=e.response,
                )
            else:
                logger.error(
                    f"Embedding API HTTP 错误: {e.response.status_code} - {error_body}"
                )
                raise EmbeddingAPIError(
                    f"Embedding API 返回错误: {e.response.status_code} - {error_body}",
                    status_code=e.response.status_code,
                    response=e.response,
                )

        except httpx.ConnectError as e:
            logger.error(f"Embedding API 连接错误: {e}")
            raise EmbeddingAPIError(f"无法连接到 Embedding API: {e}")

        except httpx.TimeoutException as e:
            logger.error(f"Embedding API 超时: {e}")
            raise EmbeddingAPIError(f"Embedding API 请求超时: {e}")

        except EmbeddingAPIError:
            raise

        except Exception as e:
            logger.error(f"Embedding API 调用异常: {e}")
            raise EmbeddingAPIError(f"Embedding API 调用失败: {e}")

        # 解析响应
        try:
            embeddings = []
            for item in response_data.get("data", []):
                embeddings.append(item.get("embedding", []))

            if len(embeddings) != len(texts):
                raise EmbeddingAPIError(
                    "Embedding JSON response embedding count mismatch: "
                    f"expected {len(texts)}, actual {len(embeddings)}",
                    status_code=415,
                )

            if not embeddings:
                raise EmbeddingAPIError(
                    "Embedding JSON response is empty", status_code=415
                )

            actual_dimensions = {len(embedding) for embedding in embeddings}
            if actual_dimensions != {self._settings.dimension}:
                actual_dimension = sorted(actual_dimensions)
                logger.error(
                    "EMBEDDING_DIMENSION_CONTRACT_MISMATCH: model=%s %s:%s actual=%s",
                    self._settings.model,
                    self._settings.base_url,
                    "/embeddings",
                    actual_dimension,
                )
                self._record_dimension_contract_violation(
                    protocol="json",
                    expected_dimension=self._settings.dimension,
                    actual_dimensions=actual_dimensions,
                )
                raise EmbeddingDimensionContractError(
                    "Embedding JSON response dimension mismatch: expected "
                    f"{self._settings.dimension}, actual values={actual_dimension}",
                    status_code=415,
                )

            for index, embedding in enumerate(embeddings):
                try:
                    values = [float(value) for value in embedding]
                except (TypeError, ValueError):
                    raise EmbeddingAPIError(
                        f"Embedding JSON response value at index {index} is invalid",
                        status_code=415,
                    ) from None
                if not all(math.isfinite(value) for value in values):
                    raise EmbeddingAPIError(
                        f"Embedding JSON response contains non-finite values at index {index}",
                        status_code=415,
                    )
                embeddings[index] = values

            self._record_wire_protocol("json")
            return embeddings

        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"解析 Embedding API 响应失败: {e}")
            raise EmbeddingAPIError(
                "Embedding JSON response is invalid", status_code=415
            ) from e

    def _should_retry(self, status_code: Optional[int], attempt: int) -> bool:
        """
        判断是否应该重试

        可重试的情况：
        - 429 Rate Limit
        - 5xx 服务器错误
        - 未达到最大重试次数

        Args:
            status_code: HTTP 状态码
            attempt: 当前尝试次数（从 0 开始）

        Returns:
            bool: 是否应该重试
        """
        if attempt >= self._max_retries:
            return False

        if status_code is None:
            # 连接错误，可重试
            return True

        # 429 Rate Limit - 可重试
        if status_code == 429:
            return True

        # 5xx 服务器错误 - 可重试
        if 500 <= status_code < 600:
            return True

        # 其他 4xx 错误 - 不可重试
        return False

    def _calculate_delay(
        self, attempt: int, response: Optional[httpx.Response] = None
    ) -> int:
        """
        计算指数退避延迟时间

        优先使用 Retry-After header，如果没有则使用指数退避。
        使用公式：delay = min(base_delay * 2^attempt, max_delay)
        加上 0-25% 的随机抖动避免惊群效应

        Args:
            attempt: 当前尝试次数（从 0 开始）
            response: HTTP 响应（可选，用于提取 Retry-After）

        Returns:
            int: 延迟时间（毫秒）
        """
        import random

        # 优先使用 Retry-After header
        if response is not None:
            retry_after_seconds = self._extract_retry_after(response)
            if retry_after_seconds is not None:
                # Retry-After 是秒，转换为毫秒
                delay_ms = retry_after_seconds * 1000
                logger.info(
                    f"Using Retry-After header: {retry_after_seconds}s ({delay_ms}ms)"
                )
                # 仍然加上小抖动（0-10%）
                jitter = random.uniform(0, 0.1) * delay_ms
                return int(delay_ms + jitter)

        # 指数退避
        delay = self._base_delay_ms * (2**attempt)

        # 加上随机抖动 (0-25%)
        jitter = random.uniform(0, 0.25) * delay
        delay = int(delay + jitter)

        # 限制最大延迟
        return min(delay, self._max_delay_ms)

    @property
    def dimension(self) -> int:
        """
        返回向量维度

        Returns:
            int: 向量维度
        """
        return self._settings.dimension

    @property
    def model(self) -> Optional[str]:
        """
        返回使用的模型名称

        Returns:
            Optional[str]: 模型名称
        """
        return self._settings.model

    def _record_wire_protocol(self, protocol: str) -> None:
        """Application adapter may observe the negotiated response type."""

    def _record_dimension_contract_violation(
        self,
        *,
        protocol: str,
        expected_dimension: int,
        actual_dimensions: set[int],
    ) -> None:
        """Application adapters may expose dimension-contract failures."""

    def _record_success(self, latency_ms: int) -> None:
        """Application adapter may observe a successful request."""

    def _record_failure(self, error: EmbeddingAPIError) -> None:
        """Application adapter may observe retry exhaustion."""


__all__ = [
    "RealEmbeddingProvider",
    "EmbeddingAPIError",
    "EmbeddingDimensionContractError",
]
