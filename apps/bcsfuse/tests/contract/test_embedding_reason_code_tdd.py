from __future__ import annotations

import json
import threading

import httpx
import pytest
from search_core.providers.embedding import EmbeddingAPIError


def rpm_limited_response() -> httpx.Response:
    return httpx.Response(
        429,
        json={
            "error": {
                "type": "RateLimitError",
                "param": json.dumps({"errorCode": "RPM_LIMIT_EXCEEDED"}),
                "code": "429",
                "integerCode": 429,
            }
        },
    )


def test_embedding_api_error_extracts_provider_reason_code_from_429_body():
    error = EmbeddingAPIError(
        "quota exhausted",
        status_code=429,
        response=rpm_limited_response(),
    )
    assert error.provider_reason_code == "RPM_LIMIT_EXCEEDED"


def test_embedding_reason_code_is_preserved_in_semantic_failure_metadata():
    error = EmbeddingAPIError(
        "quota exhausted",
        status_code=429,
        response=rpm_limited_response(),
    )
    from app.application.services.semantic_failure_metadata import (
        semantic_failure_metadata,
    )

    metadata = semantic_failure_metadata(error)
    assert metadata["semantic_status_code"] == 429
    assert metadata["semantic_reason_code"] == "embedding_quota_exhausted"
    assert metadata["semantic_provider_reason_code"] == "RPM_LIMIT_EXCEEDED"


class RateLimitedBackend:
    dimension = 64
    model = "reason-code-embedding"
    closed = False

    def embed(self, text: str) -> list[float]:
        raise EmbeddingAPIError(
            "quota exhausted",
            status_code=429,
            response=rpm_limited_response(),
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [self.embed("batch")] * len(texts)

    def close(self) -> None:
        self.closed = True


def test_circuit_breaker_preserves_provider_reason_code_in_quota_error():
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
        EmbeddingQuotaExhaustedError,
    )

    class MemoryCapacityStore:
        def __init__(self) -> None:
            self.state = ("available", 0.0)

        def observe(self, model: str) -> tuple[str, float]:
            return self.state

        def mark_exhausted(
            self,
            model: str,
            *,
            blocked_until_epoch_seconds: float,
            reason_code: str,
        ) -> tuple[str, float]:
            self.state = (reason_code, blocked_until_epoch_seconds)
            return self.state

    provider = CircuitBreakerEmbeddingProvider(
        RateLimitedBackend(),
        failure_threshold=3,
        recovery_timeout_seconds=1,
        capacity_store=MemoryCapacityStore(),
        quota_recovery_seconds=10,
        clock=lambda: 100.0,
    )
    with pytest.raises(EmbeddingQuotaExhaustedError) as captured:
        provider.embed("reader")
    assert captured.value.status_code == 429
    assert captured.value.retry_after_seconds == 10.0
    assert captured.value.reason_code == "embedding_quota_exhausted"
    assert captured.value.provider_reason_code == "RPM_LIMIT_EXCEEDED"


class SharedAdmissionLimitedEmbedding:
    def embed(self, text: str) -> list[float]:
        from app.infra.public.embedding.circuit_breaker_provider import (
            EmbeddingQuotaExhaustedError,
        )

        raise EmbeddingQuotaExhaustedError(
            "cluster call budget exhausted",
            retry_after_seconds=60.0,
            reason_code="embedding_rate_limit_admission_rejected",
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


def test_agent_search_preserves_admission_reason_code_and_retry_after() -> None:
    import asyncio

    from app.application.agent_search_service import AgentSearchCommand, AgentSearchService

    class KeywordOnlyMatch:
        def match(self, **kwargs):
            kwargs["runtime_config"]["_retrieval"] = {
                "keyword_search_used": True,
                "literal_fallback_used": True,
            }
            return []

    service = AgentSearchService(
        embedding_generator=SharedAdmissionLimitedEmbedding(),
        vector_match_service=KeywordOnlyMatch(),
    )
    outcome = asyncio.run(service.search(AgentSearchCommand(query="reader expert", top_k=3)))

    assert outcome.metadata["semantic_status_code"] == 429
    assert (
        outcome.metadata["semantic_reason_code"]
        == "embedding_rate_limit_admission_rejected"
    )
    assert outcome.metadata["semantic_retry_after_seconds"] == 60.0
    assert "embedding_rate_limit_admission_rejected" in outcome.metadata["reasons"]
    assert "embedding_quota_exhausted" not in outcome.metadata["reasons"]
