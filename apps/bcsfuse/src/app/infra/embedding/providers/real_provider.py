"""TC embedding configuration and diagnostics around the shared HTTP provider."""

from app.infra.config.search_provider_settings import embedding_options
from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from search_core.providers.embedding import (
    RealEmbeddingProvider as SharedEmbeddingProvider,
    EmbeddingAPIError,
)

# Existing tests/callers patch these shared library objects.
from search_core.providers.embedding import httpx, time


class RealEmbeddingProvider(SharedEmbeddingProvider):
    def __init__(
        self,
        settings=None,
        max_retries=4,
        base_delay_ms=5000,
        max_delay_ms=60000,
        request_interval_ms=1200,
        **kwargs,
    ):
        super().__init__(
            settings=settings or EmbeddingSettings(**kwargs),
            max_delay_ms=max_delay_ms,
            **embedding_options(max_retries, base_delay_ms, request_interval_ms),
        )

    def _record_success(self, latency_ms):
        from app.infra.observability.service_counters import get_service_counters
        from app.infra.observability.strict_mode_checker import get_strict_mode_checker

        counters = get_service_counters()
        counters.increment_embedding_real_call()

        # 记录到 strict mode checker
        checker = get_strict_mode_checker()
        checker.check_embedding_after_call(
            component="vector_match",
            was_real_call=True,
            provider="real_provider",
            latency_ms=latency_ms,
        )

    def _record_failure(self, e):
        from app.infra.observability.service_counters import get_service_counters

        counters = get_service_counters()
        counters.increment_embedding_error()

        # 记录到 fallback logger
        from app.infra.observability.fallback_logger import get_fallback_logger

        fallback_logger = get_fallback_logger()

        fallback_logger.log_fallback(
            fallback_type="embedding_unavailable",
            reason=f"API error after {self._max_retries + 1} attempts: {e}",
            affected_component="real_embedding_provider",
            severity="error",
        )
