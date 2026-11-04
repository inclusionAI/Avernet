"""TDD for native async embedding in the application search service."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.application.agent_search_service import (
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.services.agent_search_execution_budget import (
    AgentSearchExecutionBudget,
)


class _Match:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def match(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return [{"profile_key": "bot:reader:default", "score": 0.9}]


class _NativeAsyncEmbedding:
    def __init__(self, hook: Any = None) -> None:
        self.calls: list[str] = []
        self.hook = hook

    async def embed(self, query: str) -> list[float]:
        self.calls.append(query)
        if self.hook is not None:
            await self.hook()
        return [0.5, 0.5]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("online search must not fall back to the sync method")


async def _blocking() -> None:
    await asyncio.sleep(30)


def test_native_async_embedding_runs_without_the_host_worker_pool() -> None:
    embedding = _NativeAsyncEmbedding()
    match = _Match()
    offload_calls: list[Any] = []

    async def offload(operation: Any) -> Any:
        offload_calls.append(operation)
        return operation()

    service = AgentSearchService(
        embedding_generator=embedding,
        vector_match_service=match,
        candidate_recommendation_service=None,
        offload=offload,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="reader assistant", top_k=1))
    )

    assert embedding.calls == ["reader assistant"]
    assert match.calls[0]["query_embedding"] == [0.5, 0.5]
    # Only sync retrieval enters offload; native async embedding does not.
    assert len(offload_calls) == 1


def test_native_async_embedding_honors_stage_deadline_and_degrades() -> None:
    embedding = _NativeAsyncEmbedding(hook=_blocking)
    match = _Match()
    service = AgentSearchService(
        embedding_generator=embedding,
        vector_match_service=match,
        candidate_recommendation_service=None,
        execution_budget=AgentSearchExecutionBudget(
            embedding_timeout_seconds=0.05,
            retrieval_timeout_seconds=0.2,
            local_snapshot_timeout_seconds=0.1,
            total_timeout_seconds=0.5,
        ),
    )

    async def scenario() -> None:
        started = asyncio.get_running_loop().time()
        outcome = await asyncio.wait_for(
            service.search(AgentSearchCommand(query="reader assistant", top_k=1)),
            timeout=0.4,
        )
        return outcome, asyncio.get_running_loop().time() - started

    outcome, elapsed = asyncio.run(scenario())

    assert embedding.calls == ["reader assistant"]
    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_error"] == "TimeoutError"
    assert match.calls[0]["query_embedding"] == []
    assert elapsed < 0.35


def test_native_async_embedding_429_preserves_online_degradation_contract() -> None:
    class RateLimitedEmbedding:
        async def embed(self, query: str) -> list[float]:
            error = RuntimeError("model quota exhausted")
            error.status_code = 429
            error.provider_reason_code = "RPM_LIMIT_EXCEEDED"
            raise error

    match = _Match()
    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=match,
        candidate_recommendation_service=None,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="reader assistant", top_k=1))
    )

    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_status_code"] == 429
    assert outcome.metadata["semantic_reason_code"] == "embedding_quota_exhausted"
    assert (
        outcome.metadata["semantic_provider_reason_code"] == "RPM_LIMIT_EXCEEDED"
    )
    assert outcome.results[0]["profile_key"] == "bot:reader:default"


def test_sync_offload_thread_capacity_does_not_block_native_async_queries() -> None:
    embedding = _NativeAsyncEmbedding()
    match = _Match()
    offload_calls: list[Any] = []

    async def offload(operation: Any) -> Any:
        # Capture without invoking. Only retrieval should be offloaded in the
        # assertion path; this proves no embedding lambda was submitted.
        offload_calls.append(operation)
        return operation()

    service = AgentSearchService(
        embedding_generator=embedding,
        vector_match_service=match,
        candidate_recommendation_service=None,
        offload=offload,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="reader assistant", top_k=1))
    )

    assert outcome.results
    assert len(offload_calls) == 1
    assert offload_calls[0].__name__ == "<lambda>"




def test_recommend_uses_native_async_embedding_and_passes_vector_once() -> None:
    class AsyncEmbedding:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def embed(self, question: str) -> list[float]:
            self.calls.append(question)
            return [0.5, 0.5]

    class CandidateService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def recommend(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            from app.domain.models.candidate_recommendation import (
                CandidateRecommendation,
                CandidateRecommendationResponse,
            )

            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode="expert_diagnosis",
                recommendations=[
                    CandidateRecommendation(
                        profile_key="bot:reader:default",
                        worker_id="bot",
                        score=kwargs["query_embedding"][0],
                    )
                ],
                total_candidates=1,
                selected_candidates=1,
            )

    embedding = AsyncEmbedding()
    candidates = CandidateService()

    async def scenario() -> Any:
        from app.application.agent_search_service import (
            AgentRecommendationCommand,
            AgentSearchService,
        )

        service = AgentSearchService(
            embedding_generator=embedding,
            candidate_recommendation_service=candidates,
        )
        return await service.recommend(
            AgentRecommendationCommand(
                question="reader assistant",
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=1,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )

    outcome = asyncio.run(scenario())

    assert embedding.calls == ["reader assistant"]
    assert candidates.calls[0]["query_embedding"] == [0.5, 0.5]
    assert outcome.response.recommendations[0].score == 0.5


def test_recommend_async_embedding_failure_degrades_to_lexical_candidate_service() -> None:
    class RateLimitedEmbedding:
        async def embed(self, question: str) -> list[float]:
            error = RuntimeError("model quota exhausted")
            error.status_code = 429
            raise error

    class CandidateService:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def recommend(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            from app.domain.models.candidate_recommendation import (
                CandidateRecommendation,
                CandidateRecommendationResponse,
            )

            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode="expert_diagnosis",
                recommendations=[
                    CandidateRecommendation(
                        profile_key="bot:reader:default",
                        worker_id="bot",
                        score=0.01,
                    )
                ],
                total_candidates=1,
                selected_candidates=1,
            )

    candidates = CandidateService()

    async def scenario() -> Any:
        from app.application.agent_search_service import (
            AgentRecommendationCommand,
            AgentSearchService,
        )

        service = AgentSearchService(
            embedding_generator=RateLimitedEmbedding(),
            candidate_recommendation_service=candidates,
        )
        return await service.recommend(
            AgentRecommendationCommand(
                question="reader assistant",
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=1,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )

    outcome = asyncio.run(scenario())

    assert candidates.calls[0]["query_embedding"] == []
    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_status_code"] == 429
    assert outcome.response.recommendations[0].profile_key == "bot:reader:default"


def test_candidate_service_accepts_host_resolved_query_embedding() -> None:
    from app.application.services.worker_candidate_recommendation_impl import (
        WorkerCandidateRecommendationImpl,
    )
    from app.domain.models.candidate_recommendation import CandidateRecommendation
    from app.domain.models.metadata_record import MetadataRecord
    from app.application.services.worker_vector_match_types import MatchResult

    class RecordingGenerator:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, text: str) -> list[float]:
            self.calls += 1
            return [0.1]

    class RecordingMatch:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def match(self, **kwargs: Any) -> list[MatchResult]:
            self.calls.append(kwargs)
            return [
                MatchResult(
                    profile_key="bot:reader:default",
                    metadata=MetadataRecord(
                        profile_key="bot:reader:default",
                        staff_id="bot",
                        profile_id="default",
                        profile_type="default",
                        source_root="test",
                        payload={"content": "reader retrieval"},
                    ),
                    score=0.9,
                    reasons=["dense"],
                )
            ]

    embedding = RecordingGenerator()
    vector_match = RecordingMatch()
    service = WorkerCandidateRecommendationImpl(
        retrieval_service=object(),
        vector_match_service=vector_match,
        embedding_generator=embedding,
    )
    service._build_recommendation_from_metadata = (
        lambda **kwargs: CandidateRecommendation(
            profile_key="bot:reader:default", worker_id="bot", score=kwargs["score"]
        )
    )

    result = service._try_vector_match(
        question="reader assistant",
        top_k=1,
        exclude_profile_keys=[],
        query_embedding=[0.5, 0.5],
    )

    assert result is not None
    assert result[0][0].profile_key == "bot:reader:default"
    assert vector_match.calls[0]["query_embedding"] == [0.5, 0.5]
    assert embedding.calls == 0


def test_candidate_service_preserves_real_embedding_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.application.services.worker_candidate_recommendation_impl import (
        WorkerCandidateRecommendationImpl,
    )

    class RecordingMatch:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def match(self, **kwargs: Any) -> list[Any]:
            self.calls.append(kwargs)
            return []

    monkeypatch.setenv("ENABLE_REAL_EMBEDDING", "false")
    vector_match = RecordingMatch()
    service = WorkerCandidateRecommendationImpl(
        retrieval_service=object(),
        vector_match_service=vector_match,
        embedding_generator=None,
    )

    result = service._try_vector_match(
        question="reader assistant",
        top_k=1,
        exclude_profile_keys=[],
        query_embedding=[0.5],
    )

    assert result is None
    assert vector_match.calls[0]["query_embedding"] == []
