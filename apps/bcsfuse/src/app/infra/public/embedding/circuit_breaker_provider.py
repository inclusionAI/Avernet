"""Process- and cluster-aware circuit breaker for embedding providers."""

from __future__ import annotations

import logging
import threading
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
    """The process-local embedding circuit is open and this call was rejected."""


class EmbeddingQuotaExhaustedError(RuntimeError):
    """The shared model quota window is exhausted; do not call the provider."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float,
        reason_code: str = "embedding_quota_exhausted",
        provider_reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        if retry_after_seconds <= 0:
            raise ValueError("retry_after_seconds must be positive")
        self.retry_after_seconds = float(retry_after_seconds)
        self.reason_code = reason_code
        self.provider_reason_code = provider_reason_code
        self.status_code = 429


def _capacity_retry_after(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    if response is None:
        return None
    try:
        value = response.headers.get("Retry-After")
    except Exception:
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


class CircuitBreakerEmbeddingProvider:
    """Fail fast after embedded-model failures while callers own fallback.

    A process-local circuit limits concurrent probing by one replica. The
    optional capacity store adds a cluster-level condition for admission
    failures such as HTTP 429: once any replica observes quota exhaustion, all
    replicas fail fast until the shared expiry. Capacity coordination must fail
    open when its durable store is unavailable; the local circuit remains as
    the final safety net.
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
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("embedding circuit failure_threshold must be positive")
        if recovery_timeout_seconds <= 0:
            raise ValueError(
                "embedding circuit recovery_timeout_seconds must be positive"
            )
        if quota_recovery_seconds <= 0:
            raise ValueError("embedding quota_recovery_seconds must be positive")
        if max_calls_per_minute < 0:
            raise ValueError(
                "embedding max_calls_per_minute cannot be negative"
            )
        if quota_period_seconds <= 0:
            raise ValueError(
                "embedding quota_period_seconds must be positive"
            )

        self.backend = backend
        self.max_calls_per_minute = max_calls_per_minute
        self.quota_period_seconds = quota_period_seconds
        self.clock = clock
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.capacity_store = capacity_store
        self.quota_recovery_seconds = quota_recovery_seconds
        self.state = "closed"
        self._failed_attempts = 0
        self._state_changed_at = 0.0
        self._lock = threading.RLock()
        settings = getattr(backend, "_settings", None)
        self.model = str(
            getattr(backend, "model", None)
            or getattr(settings, "model", None)
            or "unknown"
        )
        set_embedding_circuit_open(self.model, open_=False)

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
        record_embedding_circuit_transition(self.model, from_state, to_state)
        set_embedding_circuit_open(self.model, open_=to_state != "closed")

    def _reject(self) -> None:
        record_embedding_circuit_rejection(self.model)
        raise EmbeddingCircuitOpenError(
            "embedding circuit is open; retry after the recovery timeout"
        )

    def _observe_shared_capacity(self) -> tuple[str, float]:
        if self.capacity_store is None:
            return ("available", 0.0)
        try:
            reason, expires_at = self.capacity_store.observe(self.model)
            return (str(reason or "available"), float(expires_at or 0.0))
        except Exception as error:
            logger.warning(
                "[EMBEDDING_CAPACITY_STORE_ERROR] operation=observe model=%s error_type=%s",
                self.model,
                type(error).__name__,
            )
            record_embedding_capacity_store_error(self.model, "observe")
            return ("available", 0.0)

    def _publish_shared_capacity(
        self, blocked_until_epoch_seconds: float, reason_code: str
    ) -> float:
        if self.capacity_store is None:
            return blocked_until_epoch_seconds
        try:
            _, expires_at = self.capacity_store.mark_exhausted(
                self.model,
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
            record_embedding_capacity_store_error(self.model, "mark_exhausted")
            return blocked_until_epoch_seconds

    def _before_local_call(self) -> None:
        with self._lock:
            if self.state == "closed":
                return
            if self.state == "half_open":
                self._reject()
            if self.clock() - self._state_changed_at < self.recovery_timeout_seconds:
                self._reject()
            self._transition("half_open")

    def _reserve_shared_call(self) -> None:
        """Reserve one upstream call from the cluster-wide RPM window.

        The limit deliberately counts provider HTTP calls, not unique queries.
        Batching therefore retains its capacity advantage while all replicas
        respect the same model-service budget.
        """

        if self.max_calls_per_minute <= 0 or self.capacity_store is None:
            return
        reserve = getattr(self.capacity_store, "reserve", None)
        if not callable(reserve):
            return
        now = self.clock()
        try:
            allowed, retry_after, reason = reserve(
                self.model,
                now_epoch_seconds=now,
                max_calls=self.max_calls_per_minute,
                period_seconds=self.quota_period_seconds,
            )
        except Exception as error:
            logger.warning(
                "[EMBEDDING_CAPACITY_STORE_ERROR] operation=reserve model=%s error_type=%s",
                self.model,
                type(error).__name__,
            )
            record_embedding_capacity_store_error(self.model, "reserve")
            return
        if allowed:
            return
        reason = str(reason or "embedding_rate_limit_admission_rejected")
        record_embedding_quota_rejection(self.model, reason)
        raise EmbeddingQuotaExhaustedError(
            "embedding model call budget is exhausted; retry after the window",
            retry_after_seconds=max(0.001, float(retry_after or 1.0)),
            reason_code=reason,
            provider_reason_code=None,
        )

    def _before_call(self) -> None:
        reason, expires_at = self._observe_shared_capacity()
        if expires_at > self.clock():
            record_embedding_quota_rejection(self.model, reason)
            raise EmbeddingQuotaExhaustedError(
                "embedding model quota is exhausted; retry after the shared expiry",
                retry_after_seconds=max(0.001, expires_at - self.clock()),
                reason_code=reason,
                provider_reason_code=None,
            )
        self._reserve_shared_call()
        self._before_local_call()

    def _after_failure(self) -> None:
        with self._lock:
            if self.state == "half_open":
                self._transition("open")
                return
            if self.state != "closed":
                # Another admitted call already opened the circuit.
                return
            self._failed_attempts += 1
            if self._failed_attempts >= self.failure_threshold:
                self._transition("open")

    def _after_success(self) -> None:
        with self._lock:
            if self.state != "closed":
                self._transition("closed", failed_attempts=0)
            else:
                self._failed_attempts = 0

    def _execute(self, operation: Callable[[], Any]) -> Any:
        try:
            result = operation()
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            self._after_failure()
            if isinstance(status_code, int) and status_code == 429:
                retry_after = _capacity_retry_after(error)
                if retry_after is None:
                    retry_after = self.quota_recovery_seconds
                blocked_until = self.clock() + retry_after
                expires_at = self._publish_shared_capacity(
                    blocked_until, "embedding_quota_exhausted"
                )
                raise EmbeddingQuotaExhaustedError(
                    "embedding model quota is exhausted; retry after the shared expiry",
                    retry_after_seconds=max(0.001, expires_at - self.clock()),
                    reason_code="embedding_quota_exhausted",
                    provider_reason_code=getattr(error, "provider_reason_code", None),
                ) from error
            raise
        self._after_success()
        return result

    def embed(self, text: str) -> list[float]:
        self._before_call()
        return self._execute(lambda: self.backend.embed(text))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return self.backend.embed_batch(texts)
        self._before_call()
        return self._execute(lambda: self.backend.embed_batch(texts))

    def close(self) -> None:
        self.backend.close()

    def __getattr__(self, item: str) -> Any:
        return getattr(self.backend, item)


__all__ = [
    "CircuitBreakerEmbeddingProvider",
    "EmbeddingCircuitOpenError",
    "EmbeddingQuotaExhaustedError",
]
