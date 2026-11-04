"""Native-async circuit and cluster capacity protection for online models."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from app.infra.public.observability.search_dependency_metrics import (
    record_embedding_capacity_store_error,
    record_embedding_circuit_rejection,
    record_embedding_circuit_transition,
    record_embedding_quota_rejection,
    set_embedding_circuit_open,
)

logger = logging.getLogger(__name__)


class EmbeddingCircuitOpenError(RuntimeError):
    """The async embedding circuit is open and this call was rejected."""


class EmbeddingQuotaExhaustedError(RuntimeError):
    """The async model quota window is exhausted; do not call the provider."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float,
        reason_code: str = "embedding_quota_exhausted",
        provider_reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = 429
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))
        self.reason_code = reason_code
        self.provider_reason_code = provider_reason_code


class AsyncCircuitBreakerEmbeddingProvider:
    """Model protection without putting native async I/O on a worker thread.

    The local circuit fails fast after repeated failures. The shared capacity
    store coordinates cluster-wide 429 windows and RPM admission. Capacity
    store failures fail open; the local circuit remains the final safety net.
    """

    def __init__(
        self,
        backend: Any,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 10.0,
        capacity_store: Any | None = None,
        quota_recovery_seconds: float = 60.0,
        max_calls_per_minute: int = 0,
        quota_period_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
        capacity_key: str | None = None,
        metric_key: str | None = None,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        if quota_recovery_seconds <= 0:
            raise ValueError("quota_recovery_seconds must be positive")
        if max_calls_per_minute < 0:
            raise ValueError("max_calls_per_minute cannot be negative")
        if quota_period_seconds <= 0:
            raise ValueError("quota_period_seconds must be positive")

        self.backend = backend
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.capacity_store = capacity_store
        settings = getattr(backend, "_settings", None)
        self.model = str(
            getattr(backend, "model", None)
            or getattr(settings, "model", None)
            or "unknown"
        )
        self.capacity_key = str(capacity_key or self.model)
        self.metric_key = str(metric_key or capacity_key or self.model)
        self.quota_recovery_seconds = quota_recovery_seconds
        self.max_calls_per_minute = max_calls_per_minute
        self.quota_period_seconds = quota_period_seconds
        self.clock = clock
        self.state = "closed"
        self._failed_attempts = 0
        self._state_changed_at = 0.0
        self._lock = asyncio.Lock()
        set_embedding_circuit_open(self.metric_key, open_=False)

    @property
    def dimension(self) -> int:
        return int(self.backend.dimension)

    def _transition(
        self, to_state: str, *, failed_attempts: int | None = None
    ) -> None:
        from_state = self.state
        self.state = to_state
        self._state_changed_at = self.clock()
        if failed_attempts is not None:
            self._failed_attempts = failed_attempts
        record_embedding_circuit_transition(self.metric_key, from_state, to_state)
        set_embedding_circuit_open(self.metric_key, open_=to_state != "closed")

    async def _reject(self) -> None:
        record_embedding_circuit_rejection(self.metric_key)
        raise EmbeddingCircuitOpenError(
            "embedding circuit is open; retry after the recovery timeout"
        )

    async def _observe_shared_capacity(self) -> tuple[str, float]:
        if self.capacity_store is None:
            return ("", 0.0)
        try:
            value = await asyncio.to_thread(
                self.capacity_store.observe, self.capacity_key
            )
            return (str(value[0]), float(value[1] or 0.0))
        except Exception as error:
            logger.warning(
                "[EMBEDDING_CAPACITY_STORE_ERROR] operation=observe model=%s error_type=%s",
                self.model,
                type(error).__name__,
            )
            record_embedding_capacity_store_error(self.metric_key, "observe")
            return ("", 0.0)

    async def _publish_shared_capacity(
        self, blocked_until_epoch_seconds: float, reason_code: str
    ) -> float:
        if self.capacity_store is None:
            return blocked_until_epoch_seconds
        try:
            _, expires_at = await asyncio.to_thread(
                self.capacity_store.mark_exhausted,
                self.capacity_key,
                blocked_until_epoch_seconds=blocked_until_epoch_seconds,
                reason_code=reason_code,
            )
            return float(expires_at)
        except Exception as error:
            logger.warning(
                "[EMBEDDING_CAPACITY_STORE_ERROR] operation=mark_exhausted model=%s error_type=%s",
                self.model,
                type(error).__name__,
            )
            record_embedding_capacity_store_error(self.metric_key, "mark_exhausted")
            return blocked_until_epoch_seconds

    async def _reserve_shared_call(self) -> None:
        if self.max_calls_per_minute <= 0 or self.capacity_store is None:
            return
        try:
            allowed, retry_after, reason = await asyncio.to_thread(
                self.capacity_store.reserve,
                self.capacity_key,
                now_epoch_seconds=self.clock(),
                max_calls=self.max_calls_per_minute,
                period_seconds=self.quota_period_seconds,
            )
        except Exception as error:
            logger.warning(
                "[EMBEDDING_CAPACITY_STORE_ERROR] operation=reserve model=%s error_type=%s",
                self.model,
                type(error).__name__,
            )
            record_embedding_capacity_store_error(self.metric_key, "reserve")
            return
        if allowed:
            return
        reason = str(reason or "embedding_rate_limit_admission_rejected")
        record_embedding_quota_rejection(self.metric_key, reason)
        raise EmbeddingQuotaExhaustedError(
            "embedding model call budget is exhausted; retry after the window",
            retry_after_seconds=max(0.001, float(retry_after or 1.0)),
            reason_code=reason,
        )

    async def _before_call(self) -> None:
        reason, expires_at = await self._observe_shared_capacity()
        if expires_at > self.clock():
            retry_after = expires_at - self.clock()
            record_embedding_quota_rejection(self.metric_key, reason)
            raise EmbeddingQuotaExhaustedError(
                "embedding model quota is exhausted; retry after the shared expiry",
                retry_after_seconds=max(0.001, retry_after),
                reason_code=str(reason or "embedding_quota_exhausted"),
            )
        await self._reserve_shared_call()
        async with self._lock:
            if self.state == "closed":
                return
            if self.state == "half_open":
                await self._reject()
            if self.clock() - self._state_changed_at < self.recovery_timeout_seconds:
                await self._reject()
            self._transition("half_open")

    async def _after_failure(self) -> None:
        async with self._lock:
            if self.state == "half_open":
                self._transition("open")
                return
            if self.state != "closed":
                return
            self._failed_attempts += 1
            if self._failed_attempts >= self.failure_threshold:
                self._transition("open")

    async def _after_success(self) -> None:
        async with self._lock:
            if self.state != "closed":
                self._transition("closed", failed_attempts=0)
            else:
                self._failed_attempts = 0

    async def _finish_success(self) -> None:
        await self._after_success()

    async def _finish_failure(self, error: Exception) -> None:
        await self._after_failure()
        status_code = getattr(error, "status_code", None)
        if not isinstance(status_code, int) or status_code != 429:
            raise error
        retry_after = getattr(error, "retry_after_seconds", None)
        if not isinstance(retry_after, (int, float)):
            retry_after = self.quota_recovery_seconds
        expires_at = await self._publish_shared_capacity(
            self.clock() + float(retry_after),
            "embedding_quota_exhausted",
        )
        record_embedding_quota_rejection(self.metric_key, "embedding_quota_exhausted")
        raise EmbeddingQuotaExhaustedError(
            "embedding model quota is exhausted; retry after the shared expiry",
            retry_after_seconds=max(0.001, expires_at - self.clock()),
            reason_code="embedding_quota_exhausted",
            provider_reason_code=getattr(error, "provider_reason_code", None),
        ) from error

    async def embed(self, text: str) -> list[float]:
        await self._before_call()
        try:
            vector = await self.backend.embed(text)
        except Exception as error:
            await self._finish_failure(error)
        else:
            await self._finish_success()
            return vector
        raise AssertionError("unreachable embedding circuit return")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        await self._before_call()
        try:
            vectors = await self.backend.embed_batch(texts)
        except Exception as error:
            await self._finish_failure(error)
        else:
            await self._finish_success()
            return vectors
        raise AssertionError("unreachable embedding circuit return")

    async def aclose(self) -> None:
        close = getattr(self.backend, "aclose", None)
        if close is not None:
            await close()


__all__ = [
    "AsyncCircuitBreakerEmbeddingProvider",
    "EmbeddingCircuitOpenError",
    "EmbeddingQuotaExhaustedError",
]
