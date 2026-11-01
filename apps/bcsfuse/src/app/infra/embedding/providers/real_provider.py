"""TC embedding configuration and diagnostics around the shared HTTP provider."""

from time import perf_counter

from app.infra.config.search_provider_settings import embedding_options
from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.public.observability.search_dependency_metrics import (
    record_embedding_call,
)
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
        max_retries=1,
        base_delay_ms=250,
        max_delay_ms=2000,
        request_interval_ms=0,
        retry_budget_ms=2000,
        **kwargs,
    ):
        super().__init__(
            settings=settings or EmbeddingSettings(**kwargs),
            max_delay_ms=max_delay_ms,
            **embedding_options(
                max_retries,
                base_delay_ms,
                request_interval_ms,
                retry_budget_ms,
            ),
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Record real embedding dependency outcomes without hiding errors."""
        if not texts:
            return super().embed_batch(texts)
        started_at = perf_counter()
        try:
            result = super().embed_batch(texts)
        except Exception:
            record_embedding_call(
                self._settings.model, "error", perf_counter() - started_at
            )
            raise
        record_embedding_call(
            self._settings.model, "success", perf_counter() - started_at
        )
        return result

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

    def _record_dimension_contract_violation(
        self,
        *,
        protocol: str,
        expected_dimension: int,
        actual_dimensions: set[int],
    ) -> None:
        from app.infra.public.observability.search_dependency_metrics import (
            record_embedding_dimension_contract_violation,
        )

        record_embedding_dimension_contract_violation(
            self._settings.model,
            protocol,
            expected_dimension=expected_dimension,
            actual_dimensions=actual_dimensions,
        )

    def _record_wire_protocol(self, protocol: str):
        from app.infra.public.observability.search_dependency_metrics import (
            record_embedding_wire_response,
        )

        record_embedding_wire_response(self._settings.model, protocol)

    def _record_failure(self, e):
        from app.infra.observability.service_counters import get_service_counters

        counters = get_service_counters()
        counters.increment_embedding_error()

        # 记录到 fallback logger
        from app.infra.observability.fallback_logger import get_fallback_logger

        fallback_logger = get_fallback_logger()

        fallback_logger.log_fallback(
            fallback_type="embedding_unavailable",
            reason=f"embedding provider call failed; retry policy stopped: {e}",
            affected_component="real_embedding_provider",
            severity="error",
        )
