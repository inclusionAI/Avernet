"""Stateless token-lane routing for the native async embedding provider."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.infra.public.embedding.async_circuit_breaker_provider import (
    EmbeddingQuotaExhaustedError,
)


class AsyncEmbeddingTokenPoolProvider:
    """Route one online embedding call or batch to one token lane.

    Every Pod receives the same lane list. Routing is round robin so added Pods
    spread requests over the shared pool; no Pod owns a token. A 429 or circuit
    failure moves the same call to another lane before the pool reports the
    exhausted state to its normal degraded-search contract.
    """

    def __init__(
        self,
        lanes: Sequence[Any],
        *,
        max_failover_attempts: int = 4,
    ) -> None:
        normalized = list(lanes)
        if not normalized:
            raise ValueError("embedding token pool requires at least one token lane")
        if max_failover_attempts <= 0:
            raise ValueError(
                "embedding token pool max_failover_attempts must be positive"
            )
        dimensions = {int(lane.dimension) for lane in normalized}
        if len(dimensions) != 1:
            raise ValueError("all embedding token lanes must use the same dimension")
        self.lanes = normalized
        self.dimension = dimensions.pop()
        self.model = str(
            getattr(normalized[0], "model", None) or "embedding-token-pool"
        )
        self.max_failover_attempts = min(
            max_failover_attempts, len(normalized)
        )
        self._cursor = 0

    @property
    def lane_count(self) -> int:
        return len(self.lanes)

    def _ordered_lanes(self) -> list[Any]:
        count = len(self.lanes)
        start = self._cursor
        attempts = min(self.max_failover_attempts, count)
        self._cursor = (start + 1) % count
        return [self.lanes[(start + index) % count] for index in range(attempts)]

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        errors: list[BaseException] = []
        for lane in self._ordered_lanes():
            try:
                return await _invoke_batch(lane, texts)
            except (Exception, asyncio.CancelledError) as error:
                if isinstance(error, asyncio.CancelledError) and _caller_cancelled():
                    raise
                errors.append(error)

        _raise_pool_error(errors, "async")

    async def aclose(self) -> None:
        for lane in self.lanes:
            close = getattr(lane, "aclose", None)
            if close is not None:
                await close()



def _caller_cancelled() -> bool:
    """Distinguish a backend cancellation from cancellation of this task.

    Async transports expose request timeouts as ``CancelledError``. A timeout
    raised by the lane should fail over. If the host task itself was cancelled,
    the TokenPool must not swallow the cancellation and continue to call model
    lanes.
    """

    task = asyncio.current_task()
    cancelling = getattr(task, "cancelling", None)
    return bool(task is not None and callable(cancelling) and cancelling() > 0)

async def _invoke_batch(lane: Any, texts: list[str]) -> list[list[float]]:
    vectors = await lane.embed_batch(texts)
    if len(vectors) != len(texts):
        raise ValueError(
            "embedding token lane returned a mismatched vector count"
        )
    return vectors


def _raise_pool_error(
    errors: list[BaseException], transport: str
) -> None:
    if not errors:
        raise RuntimeError(f"{transport} embedding token pool produced no result")

    quota_errors = [error for error in errors if _is_quota_error(error)]
    if quota_errors:
        retry_after = min(
            float(getattr(error, "retry_after_seconds", 1.0))
            for error in quota_errors
        )
        provider_reason = next(
            (
                getattr(error, "provider_reason_code", None)
                for error in quota_errors
                if getattr(error, "provider_reason_code", None) is not None
            ),
            None,
        )
        raise EmbeddingQuotaExhaustedError(
            "all embedding token lanes are exhausted; retry after the shortest shared window",
            retry_after_seconds=max(0.001, retry_after),
            reason_code="embedding_quota_exhausted",
            provider_reason_code=provider_reason,
        ) from quota_errors[0]

    raise errors[-1]


def _is_quota_error(error: BaseException) -> bool:
    if isinstance(error, EmbeddingQuotaExhaustedError):
        return True
    return isinstance(getattr(error, "status_code", None), int) and getattr(
        error, "status_code"
    ) == 429


__all__ = ["AsyncEmbeddingTokenPoolProvider"]
