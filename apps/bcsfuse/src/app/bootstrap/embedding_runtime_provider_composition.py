"""Compose deployment embedding providers without expanding startup assembly."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.bootstrap.embedding_token_pool_settings import (
    resolve_embedding_token_pool,
    token_lane_key,
)
from app.infra.embedding.config.embedding_settings import EmbeddingSettings

logger = logging.getLogger(__name__)


def _int(config: Any, key: str, default: int) -> int:
    return int(config.get_int(key, default))


def _float(config: Any, key: str, default: float) -> float:
    return float(config.get_float(key, default))


def _settings(
    *,
    base_url: str,
    token: str,
    model: str,
    dimension: int,
    request_dimensions: bool,
    timeout_ms: int | None = None,
) -> EmbeddingSettings:
    data = {
        "base_url": base_url,
        "auth_token": token,
        "model": model,
        "dimension": dimension,
        "request_dimensions": request_dimensions,
    }
    if timeout_ms is not None:
        data["timeout_ms"] = timeout_ms
    return EmbeddingSettings(**data)


def _capacity_key(model: str, token: str) -> str:
    return f"{model}:{token_lane_key(token)}"


def register_embedding_runtime_providers(
    registry: Any,
    config: Any,
    *,
    embedding_base_url: str,
    embedding_model: str,
    embedding_dimension: int,
    embedding_request_dimensions: bool,
) -> None:
    """Register shared-pool sync indexing and native-async online providers.

    Every process reads the same token-pool configuration and creates one
    capacity lane per token. No Pod is assigned a token: all Pods round robin
    the same lanes and share the durable store by token-scoped capacity keys.
    """

    if not bool(config.get("embedding.cache.enabled", True)):
        raise RuntimeError(
            "embedding.cache.enabled must be true for runtime serving; "
            "the query cache is a required capacity contract"
        )

    token_pool = resolve_embedding_token_pool(config, os.environ)
    online_timeout_ms = int(
        _float(config, "search.execution.embedding_timeout_seconds", 1.50)
        * 1000
    )
    settings_pool = [
        _settings(
            base_url=embedding_base_url,
            token=token,
            model=embedding_model,
            dimension=embedding_dimension,
            request_dimensions=embedding_request_dimensions,
        )
        for token in token_pool
    ]

    async_settings_pool = [
        _settings(
            base_url=embedding_base_url,
            token=token,
            model=embedding_model,
            dimension=embedding_dimension,
            request_dimensions=embedding_request_dimensions,
            timeout_ms=online_timeout_ms,
        )
        for token in token_pool
    ]

    if not embedding_base_url or not settings_pool:
        from app.infra.embedding.providers.fake_provider import FakeEmbeddingProvider

        provider = FakeEmbeddingProvider(dimension=embedding_dimension)
        registry.register("embedding_provider", provider)
        registry.register("agent_online_embedding_provider", provider)
        logger.warning(
            "Embedding is not configured; using the deterministic fake provider "
            "for both indexing and online search"
        )
        return

    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )
    from app.infra.public.embedding.embedding_token_pool_provider import (
        EmbeddingTokenPoolProvider,
    )
    from app.infra.public.embedding.real_embedding_provider import (
        RealEmbeddingProvider,
    )

    capacity_store = registry.get("embedding_capacity_store")
    sync_lanes = [
        CircuitBreakerEmbeddingProvider(
            RealEmbeddingProvider(settings=settings),
            failure_threshold=_int(config, "embedding.circuit.failure_threshold", 3),
            recovery_timeout_seconds=_float(
                config, "embedding.circuit.recovery_seconds", 10.0
            ),
            capacity_store=capacity_store,
            quota_recovery_seconds=_float(
                config, "embedding.quota.recovery_seconds", 60.0
            ),
            max_calls_per_minute=_int(
                config, "embedding.quota.max_calls_per_minute", 30
            ),
            quota_period_seconds=_float(config, "embedding.quota.period_seconds", 60.0),
            capacity_key=_capacity_key(embedding_model, settings.auth_token),
            metric_key=_capacity_key(embedding_model, settings.auth_token),
        )
        for settings in settings_pool
    ]
    sync_provider = CachedEmbeddingProvider(
        EmbeddingTokenPoolProvider(sync_lanes),
        max_entries=_int(config, "embedding.cache.max_entries", 10000),
        ttl_seconds=_float(config, "embedding.cache.ttl_seconds", 300.0),
        max_query_batch_size=_int(
            config, "embedding.cache.max_query_batch_size", 32
        ),
        query_batch_wait_seconds=_float(
            config, "embedding.cache.query_batch_wait_seconds", 0.05
        ),
        query_batch_peak_wait_seconds=_float(
            config, "embedding.cache.query_batch_peak_wait_seconds", 0.15
        ),
        query_batch_peak_threshold=_int(
            config, "embedding.cache.query_batch_peak_threshold", 8
        ),
        max_query_batch_workers=_int(
            config, "embedding.cache.max_query_batch_workers", 1
        ),
    )
    registry.register("embedding_provider", sync_provider)

    from search_core.providers.embedding import AsyncHttpEmbedding

    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )
    from app.infra.public.embedding.async_circuit_breaker_provider import (
        AsyncCircuitBreakerEmbeddingProvider,
    )
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )
    from app.infra.public.embedding.cached_async_embedding_provider import (
        CachedAsyncEmbeddingProvider,
    )

    max_failover_attempts = _int(
        config, "embedding.token_pool.max_failover_attempts", 4
    )
    async_lanes = [
        AsyncCircuitBreakerEmbeddingProvider(
            AsyncHttpEmbedding(settings),
            failure_threshold=_int(config, "embedding.circuit.failure_threshold", 3),
            recovery_timeout_seconds=_float(
                config, "embedding.circuit.recovery_seconds", 10.0
            ),
            capacity_store=capacity_store,
            quota_recovery_seconds=_float(
                config, "embedding.quota.recovery_seconds", 60.0
            ),
            max_calls_per_minute=_int(
                config, "embedding.quota.max_calls_per_minute", 30
            ),
            quota_period_seconds=_float(config, "embedding.quota.period_seconds", 60.0),
            capacity_key=_capacity_key(embedding_model, settings.auth_token),
            metric_key=_capacity_key(embedding_model, settings.auth_token),
        )
        for settings in async_settings_pool
    ]
    batched_provider = AsyncBatchingEmbeddingProvider(
        AsyncEmbeddingTokenPoolProvider(
            async_lanes,
            max_failover_attempts=max_failover_attempts,
        ),
        max_batch_size=_int(config, "embedding.cache.max_query_batch_size", 32),
        batch_wait_seconds=_float(
            config,
            "embedding.cache.query_batch_wait_seconds",
            0.05,
        ),
        max_workers=_int(config, "embedding.cache.max_query_batch_workers", 4),
    )
    online_provider = CachedAsyncEmbeddingProvider(
        batched_provider,
        max_entries=_int(config, "embedding.cache.max_entries", 10000),
        ttl_seconds=_float(config, "embedding.cache.ttl_seconds", 300.0),
    )
    registry.register("agent_online_embedding_provider", online_provider)

    startup = registry.get("startup_provider")
    if startup is not None:
        from app.providers.public.async_model_shutdown_provider import (
            AsyncModelShutdownProvider,
        )

        registry.register(
            "startup_provider",
            AsyncModelShutdownProvider(startup, provider=online_provider),
        )
    logger.info(
        "[EMBEDDING_RUNTIME_COMPOSITION] "
        "sync_provider=CachedEmbeddingProvider(TokenPool) "
        "online_provider=CachedAsyncEmbeddingProvider(BatchingTokenPool) "
        "model=%s dimension=%s lane_count=%d shared_capacity=%s",
        embedding_model,
        embedding_dimension,
        len(async_lanes),
        "durable" if capacity_store is not None else "unconfigured",
    )


__all__ = ["register_embedding_runtime_providers"]
