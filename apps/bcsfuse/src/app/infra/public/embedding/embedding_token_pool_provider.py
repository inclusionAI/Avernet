"""Stateless token-lane routing for synchronous indexing embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.infra.public.embedding.circuit_breaker_provider import (
    EmbeddingQuotaExhaustedError,
)


class _PoolSettings:
    """Expose only the non-secret cache descriptor, never a credentials list."""

    def __init__(self, model: str, dimension: int) -> None:
        self.model = model
        self.dimension = dimension


class EmbeddingTokenPoolProvider:
    """Rotate the same deployment token pool for indexing and sync calls."""

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
        self.model = str(
            getattr(normalized[0], "model", None) or "embedding-token-pool"
        )
        self.dimension = dimensions.pop()
        self._settings = _PoolSettings(self.model, self.dimension)
        first_settings = getattr(normalized[0], "_settings", None)
        if first_settings is not None:
            self._settings = first_settings
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

    def embed(self, text: str) -> list[float]:
        errors: list[BaseException] = []
        for lane in self._ordered_lanes():
            try:
                return list(lane.embed(text))
            except Exception as error:
                errors.append(error)
        _raise_sync_pool_error(errors)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        errors: list[BaseException] = []
        for lane in self._ordered_lanes():
            try:
                vectors = lane.embed_batch(texts)
                if len(vectors) != len(texts):
                    raise ValueError(
                        "embedding token lane returned a mismatched vector count"
                    )
                return vectors
            except Exception as error:
                errors.append(error)

        _raise_sync_pool_error(errors)

    def close(self) -> None:
        closed_any = False
        for lane in self.lanes:
            close = getattr(lane, "close", None)
            if close is not None:
                close()
                closed_any = True
        if not closed_any:
            for lane in self.lanes:
                backend = getattr(lane, "backend", None)
                close = getattr(backend, "close", None)
                if close is not None:
                    close()


def _raise_sync_pool_error(errors: list[BaseException]) -> None:
    if not errors:
        raise RuntimeError("sync embedding token pool produced no result")

    quota_errors = [
        error
        for error in errors
        if isinstance(error, EmbeddingQuotaExhaustedError)
        or getattr(error, "status_code", None) == 429
    ]
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
            "all sync embedding token lanes are exhausted",
            retry_after_seconds=max(0.001, retry_after),
            reason_code="embedding_quota_exhausted",
            provider_reason_code=provider_reason,
        ) from quota_errors[0]

    raise errors[-1]


__all__ = ["EmbeddingTokenPoolProvider"]
