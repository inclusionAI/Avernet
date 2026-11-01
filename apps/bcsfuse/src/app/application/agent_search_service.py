"""Unified application service for v1 search and recommendation.

The HTTP routes supply protocol-specific requests and projections.  This
service owns the common application policy: query normalization, default
scopes, bounded execution, fast search defaults, and stable dependency-error
semantics.  Search and recommendation remain two projections of the shared
retrieval application path rather than two unrelated route implementations.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.application.agent_search_cache import AgentSearchResultCache
from app.application.agent_search_resilience_cache import AgentSearchResilienceCache
from app.application.services.agent_search_execution_budget import (
    AgentSearchExecutionBudget,
)
from app.application.services.local_retrieval_snapshot import (
    project_snapshot_hit,
    project_snapshot_recommendation,
    snapshot_hits,
    snapshot_metadata,
)
from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from app.application.services.search_index_state import (
    INDEX_STATE_FIELDS,
    index_state_from_provider,
)
from app.application.services.worker_vector_match_types import MatchResult
from app.application.services.semantic_failure_metadata import (
    semantic_failure_metadata,
)
from app.domain.models.retrieval_mode import RetrievalMode
from app.infra.public.observability.search_dependency_metrics import (
    record_search_fallback_unavailable,
)
from app.infra.public.observability.search_stage_metrics import search_stage_metrics


def default_search_filters(
    filters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge request filters with the v1 search and recommendation defaults."""

    merged: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        # Copy mutable filter arrays without corrupting valid V1 scalar
        # aliases such as worker_id; ``list("bot:owner")`` would become a
        # character array and no longer match a payload value.
        merged[key] = (
            list(value) if isinstance(value, (list, tuple, set)) else value
        )
    merged.setdefault("availability", ["protected", "public"])
    merged.setdefault("runtime_state", ["online"])
    return merged


@dataclass(frozen=True, slots=True)
class AgentSearchCommand:
    """Transport-independent v1 search command."""

    query: str
    top_k: int
    mode: str = "auto"
    min_score: float = 0.0
    filters: Mapping[str, Any] | None = None
    reranker_model: str | None = None


MAX_RECOMMEND_EXPAND_FACTOR = 3


def _annotate_semantic_failure(metadata: dict[str, Any]) -> None:
    """Publish the stable semantic reason independent of the fallback outcome."""

    if (
        metadata.get("semantic_unavailable") is True
        and metadata.get("semantic_status_code") == 429
        and not metadata.get("semantic_reason_code")
    ):
        metadata["semantic_reason_code"] = "embedding_quota_exhausted"


def _mark_semantic_fallback(
    metadata: dict[str, Any], operation: str
) -> None:
    """Mark the terminal bounded-fallback state without inventing candidates."""

    _annotate_semantic_failure(metadata)
    if metadata.get("semantic_unavailable") is not True:
        return
    if metadata.get("fallback_unavailable") is True:
        return
    metadata["fallback_unavailable"] = True
    metadata["fallback_reason_code"] = "SEMANTIC_RETRIEVAL_UNAVAILABLE"
    record_search_fallback_unavailable(
        operation, metadata["fallback_reason_code"]
    )


def _mark_retrieval_unavailable(
    metadata: dict[str, Any], error: BaseException
) -> None:
    """Record a retrieval dependency failure before bounded recovery."""

    metadata["retrieval_unavailable"] = True
    metadata["retrieval_error"] = type(error).__name__


def _mark_terminal_search_fallback(metadata: dict[str, Any]) -> None:
    """Turn a terminal search failure into a stable, non-success envelope."""

    if metadata.get("fallback_unavailable") is True:
        return
    metadata["fallback_unavailable"] = True
    metadata["fallback_reason_code"] = "SEARCH_RETRIEVAL_UNAVAILABLE"
    record_search_fallback_unavailable(
        "search", metadata["fallback_reason_code"]
    )


def _mark_candidate_unavailable(
    metadata: dict[str, Any], error_type: str
) -> None:
    """Record a recommendation dependency failure before bounded recovery."""

    metadata["candidate_unavailable"] = True
    metadata["candidate_error"] = error_type


def _mark_terminal_recommendation_fallback(
    metadata: dict[str, Any],
) -> None:
    """Turn a terminal recommendation failure into a stable envelope."""

    if metadata.get("fallback_unavailable") is True:
        return
    metadata["fallback_unavailable"] = True
    metadata["fallback_reason_code"] = "CANDIDATE_RETRIEVAL_UNAVAILABLE"
    record_search_fallback_unavailable(
        "recommend", metadata["fallback_reason_code"]
    )


def degraded_reasons(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Return v1's stable, ordered degradation reason codes."""

    reasons: list[str] = []
    for reason in (
        "semantic_unavailable",
        "keyword_unavailable",
        "literal_fallback_used",
        "literal_fallback_default",
    ):
        if metadata.get(reason) is True and reason not in reasons:
            reasons.append(reason)
    semantic_reason_code = metadata.get("semantic_reason_code")
    if (
        metadata.get("semantic_unavailable") is True
        and isinstance(semantic_reason_code, str)
        and semantic_reason_code
        and semantic_reason_code not in reasons
    ):
        reasons.insert(
            min(1, len(reasons)),
            semantic_reason_code,
        )
    if (
        metadata.get("semantic_unavailable") is True
        and metadata.get("keyword_search_used") is True
        and "keyword_fallback_used" not in reasons
    ):
        reasons.append("keyword_fallback_used")
    if (
        metadata.get("literal_fallback_default_used") is True
        and "literal_fallback_default" not in reasons
    ):
        reasons.append("literal_fallback_default")
    if metadata.get("score_source") == "regex_fallback" and "regex_fallback" not in reasons:
        reasons.append("regex_fallback")
    for reason in (
        "retrieval_unavailable",
        "candidate_unavailable",
        "fallback_unavailable",
    ):
        if metadata.get(reason) is True and reason not in reasons:
            reasons.append(reason)
    if metadata.get("rerank_degraded") is True and "rerank_degraded" not in reasons:
        reasons.append("rerank_degraded")
    for reason in ("local_snapshot_used", "last_known_good_used"):
        if metadata.get(reason) is True and reason not in reasons:
            reasons.append(reason)
    if metadata.get("index_complete") is False:
        reasons.append("index_incomplete")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class AgentRecommendationCommand:
    """Transport-independent v1 recommendation command."""

    question: str
    top_k: int
    enable_rerank: bool
    reranker_model: str | None
    expand_factor: int
    vector_min_score: float
    rerank_min_score: float | None
    min_score_explicit: bool
    filters: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentSearchOutcome:
    """The raw retrieval result plus common execution diagnostics."""

    query: str
    normalized_query: str
    top_k: int
    mode: str
    results: tuple[Any, ...]
    metadata: dict[str, Any]
    config_applied: dict[str, Any]
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class AgentRecommendationOutcome:
    """Candidate response before the HTTP recommendation projection."""

    question: str
    response: CandidateRecommendationResponse
    config_applied: dict[str, Any]
    elapsed_ms: float
    diagnostics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


Offload = Callable[[Callable[[], Any]], Awaitable[Any]]


async def _default_offload(operation: Callable[[], Any]) -> Any:
    """Run synchronous search work away from the event loop by default."""

    return await asyncio.to_thread(operation)


def normalize_query(query: str) -> str:
    """Return a stable cache/retrieval representation without raw whitespace."""

    if not isinstance(query, str):
        raise ValueError("query must be a string")
    return " ".join(unicodedata.normalize("NFKC", query).split())


class AgentSearchService:
    """Single application entrypoint used by search and recommendation routes."""

    def __init__(
        self,
        *,
        embedding_generator: Any | None = None,
        vector_match_service: Any | None = None,
        candidate_recommendation_service: Any | None = None,
        result_cache: AgentSearchResultCache | None = None,
        resilience_cache: AgentSearchResilienceCache | None = None,
        local_snapshot: Any | None = None,
        index_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        execution_budget: AgentSearchExecutionBudget | None = None,
        offload: Offload = _default_offload,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._embedding_generator = embedding_generator
        self._vector_match_service = vector_match_service
        self._candidate_recommendation_service = candidate_recommendation_service
        self._result_cache = result_cache
        self._resilience_cache = resilience_cache
        self._local_snapshot = local_snapshot
        self._index_state_provider = index_state_provider
        self._execution_budget = execution_budget
        self._offload = offload
        self._clock = clock

    def _apply_index_state(self, metadata: dict[str, Any]) -> None:
        """Publish bounded index state without leaking provider diagnostics."""

        index_state = index_state_from_provider(self._index_state_provider)
        if index_state is None:
            return
        metadata.update(index_state)
        if index_state.get("index_complete") is False:
            metadata.setdefault("index_incomplete", True)

    def _prepared_result_cache_hit(self, hit: Any) -> Any:
        """Refresh current index state on a cached result and return it."""
        hit_metadata = (
            hit.response.metadata
            if isinstance(hit, AgentRecommendationOutcome)
            else hit.metadata
        )
        self._apply_index_state(hit_metadata)
        self._publish_status(hit_metadata)
        hit_metadata["cache_hit"] = True
        if isinstance(hit, AgentRecommendationOutcome):
            hit.metadata.update(
                cache_hit=True,
                status=hit_metadata["status"],
                reasons=hit_metadata["reasons"],
            )
        return hit

    async def _cached_or_execute(
        self,
        operation: str,
        *,
        normalized_query: str,
        material: dict[str, Any],
        metadata: dict[str, Any],
        execute: Callable[[], Awaitable[Any]],
        total_timeout_fallback: Callable[[str], Any],
    ) -> Any:
        """Serve normal, stale, negative, and live outcomes in bounded order."""

        metadata.setdefault("negative_cache_hit", False)
        cache = self._result_cache or self._resilience_cache
        if cache is None:
            return await self._bounded_execute(
                execute, total_timeout_fallback
            )

        index_version = cache.current_index_version()
        if index_version is None:
            return await self._bounded_execute(
                execute, total_timeout_fallback
            )

        key = cache.key(
            operation,
            index_version=index_version,
            material={
                "normalized_query": cache.normalize_query(normalized_query),
                **material,
            },
        )

        if self._result_cache is not None:
            hit = self._result_cache.get(key)
            if hit is not None:
                metadata["cache_hit"] = True
                return self._prepared_result_cache_hit(hit)
            self._result_cache.record_miss()

        if self._resilience_cache is not None:
            negative = self._resilience_cache.get_negative(key)
            if negative is not None:
                negative_metadata = (
                    negative.response.metadata
                    if isinstance(negative, AgentRecommendationOutcome)
                    else negative.metadata
                )
                self._apply_index_state(negative_metadata)
                negative_metadata["negative_cache_hit"] = True
                if isinstance(negative, AgentRecommendationOutcome):
                    negative.metadata["negative_cache_hit"] = True
                metadata["negative_cache_hit"] = True
                self._publish_status(negative_metadata)
                return negative

        async with cache.singleflight(key):
            if self._result_cache is not None:
                hit = self._result_cache.get_without_metrics(key)
                if hit is not None:
                    metadata["cache_hit"] = True
                    return self._prepared_result_cache_hit(hit)

            outcome = await self._bounded_execute(
                execute, total_timeout_fallback
            )
            outcome_metadata = (
                outcome.response.metadata
                if isinstance(outcome, AgentRecommendationOutcome)
                else outcome.metadata
            )
            if (
                self._result_cache is not None
                and not self._has_degradation_markers(outcome_metadata)
            ):
                self._result_cache.store(key, outcome)

            if self._resilience_cache is not None:
                if self._has_degradation_markers(outcome_metadata):
                    known_good = self._resilience_cache.get_last_known_good(
                        key
                    )
                    if known_good is not None:
                        stale, age_seconds = known_good
                        stale_metadata = (
                            stale.response.metadata
                            if isinstance(stale, AgentRecommendationOutcome)
                            else stale.metadata
                        )
                        self._apply_index_state(stale_metadata)
                        self._preserve_dependency_state(
                            outcome_metadata, stale_metadata
                        )
                        stale_metadata["last_known_good_used"] = True
                        stale_metadata["last_known_good_age_seconds"] = (
                            age_seconds
                        )
                        stale_metadata["cache_hit"] = False
                        stale.metadata["last_known_good_used"] = True
                        stale.metadata["last_known_good_age_seconds"] = (
                            age_seconds
                        )
                        stale.metadata["cache_hit"] = False
                        metadata["last_known_good_used"] = True
                        self._publish_status(stale_metadata)
                        return stale
                else:
                    self._resilience_cache.store_last_known_good(key, outcome)

                if self._terminal_fallback(
                    outcome_metadata
                ) and not self._outcome_is_empty(outcome):
                    self._resilience_cache.store_negative(key, outcome)

            outcome_metadata["cache_hit"] = False
            self._publish_status(outcome_metadata)
            return outcome

    @staticmethod
    def _has_degradation_markers(metadata: dict[str, Any]) -> bool:
        if metadata.get("keyword_unavailable") is True:
            return True
        if metadata.get("semantic_unavailable") is True:
            return True
        if metadata.get("literal_fallback_used") is True:
            return True
        if metadata.get("score_source") == "regex_fallback":
            return True
        if metadata.get("retrieval_unavailable") is True:
            return True
        if metadata.get("candidate_unavailable") is True:
            return True
        if metadata.get("local_snapshot_used") is True:
            return True
        if metadata.get("last_known_good_used") is True:
            return True
        if metadata.get("index_complete") is False:
            return True
        return metadata.get("fallback_unavailable") is True

    @staticmethod
    def _outcome_is_empty(outcome: Any) -> bool:
        if isinstance(outcome, AgentRecommendationOutcome):
            return not outcome.response.recommendations
        if isinstance(outcome, AgentSearchOutcome):
            return not outcome.results
        return True

    @staticmethod
    def _terminal_fallback(metadata: Mapping[str, Any]) -> bool:
        return metadata.get("fallback_unavailable") is True

    @staticmethod
    def _preserve_dependency_state(
        source: Mapping[str, Any], destination: dict[str, Any]
    ) -> None:
        for key in (
            "semantic_unavailable",
            "semantic_status_code",
            "semantic_reason_code",
            "semantic_retry_after_seconds",
            "keyword_unavailable",
            "keyword_search_used",
            "retrieval_unavailable",
            "retrieval_error",
            "candidate_unavailable",
            "candidate_error",
        ):
            if key in source:
                destination[key] = source[key]
        destination.pop("fallback_unavailable", None)
        destination.pop("fallback_reason_code", None)

    @staticmethod
    def _publish_status(metadata: dict[str, Any]) -> None:
        reasons = degraded_reasons(metadata)
        metadata["status"] = (
            "fallback_unavailable"
            if metadata.get("fallback_unavailable") is True
            else "degraded"
            if reasons
            else "ok"
        )
        metadata["reasons"] = list(reasons)

    @staticmethod
    def _validated_query(value: str, *, field_name: str) -> str:
        normalized = normalize_query(value)
        if not normalized:
            raise ValueError(f"{field_name} must not be blank")
        return normalized

    def _default_filters(
        self, filters: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        return default_search_filters(filters)

    async def _run(
        self,
        operation: Callable[[], Any],
        timeout_seconds: float | None,
    ) -> Any:
        if timeout_seconds is None:
            return await self._offload(operation)
        return await asyncio.wait_for(
            self._offload(operation),
            timeout=timeout_seconds,
        )

    async def _bounded_execute(
        self,
        execute: Callable[[], Awaitable[Any]],
        fallback: Callable[[str], Any],
    ) -> Any:
        if self._execution_budget is None:
            return await execute()
        try:
            return await asyncio.wait_for(
                execute(),
                timeout=self._execution_budget.total_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return fallback("TotalTimeout")

    @classmethod
    def final_search_outcome(
        cls,
        command: AgentSearchCommand,
        *,
        error_type: str,
        local_snapshot: Any | None = None,
    ) -> AgentSearchOutcome:
        """Build the last-resort search envelope from the process snapshot."""

        metadata: dict[str, Any] = {
            "retrieval_unavailable": True,
            "retrieval_error": error_type,
        }
        filters = default_search_filters(command.filters)
        runtime_config = {"reranker_model": command.reranker_model, "filters": filters}
        results: tuple[MatchResult, ...] = ()
        try:
            if local_snapshot is not None:
                hits, strategy = snapshot_hits(
                    local_snapshot,
                    query=normalize_query(command.query),
                    limit=command.top_k,
                    filters=filters,
                )
                results = tuple(project_snapshot_hit(hit) for hit in hits)
                if results:
                    metadata.update(
                        snapshot_metadata(
                            local_snapshot,
                            hit_count=len(results),
                            strategy=strategy,
                        )
                    )
                    metadata.pop("fallback_unavailable", None)
        except Exception:
            results = ()
        if not results:
            metadata["fallback_unavailable"] = True
            metadata["fallback_reason_code"] = "SEARCH_RETRIEVAL_UNAVAILABLE"
            record_search_fallback_unavailable(
                "search", metadata["fallback_reason_code"]
            )
        cls._publish_status(metadata)
        return AgentSearchOutcome(
            query=command.query,
            normalized_query=normalize_query(command.query),
            top_k=command.top_k,
            mode=command.mode,
            results=results,
            metadata=metadata,
            config_applied=runtime_config,
            elapsed_ms=0.0,
        )

    def _stage_timeout(self, stage: str) -> float | None:
        if self._execution_budget is None:
            return None
        if stage == "embedding":
            return self._execution_budget.embedding_timeout_seconds
        if stage == "retrieval":
            return self._execution_budget.retrieval_timeout_seconds
        return self._execution_budget.local_snapshot_timeout_seconds

    @staticmethod
    def _has_dependency_failure(metadata: Mapping[str, Any]) -> bool:
        return any(
            metadata.get(key) is True
            for key in (
                "semantic_unavailable",
                "keyword_unavailable",
                "retrieval_unavailable",
                "candidate_unavailable",
            )
        )

    async def _local_snapshot_search(
        self,
        *,
        query: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> tuple[MatchResult, ...]:
        if self._local_snapshot is None:
            return ()
        timeout = self._stage_timeout("local_snapshot")
        hits, strategy = await self._run(
            lambda: snapshot_hits(
                self._local_snapshot,
                query=query,
                limit=top_k,
                filters=filters,
            ),
            timeout,
        )
        results = tuple(project_snapshot_hit(hit) for hit in hits)
        self._local_snapshot_metadata = snapshot_metadata(
            self._local_snapshot,
            hit_count=len(results),
            strategy=strategy,
        )
        return results

    async def _local_snapshot_recommendations(
        self,
        *,
        question: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> list[CandidateRecommendation]:
        if self._local_snapshot is None:
            return []
        timeout = self._stage_timeout("local_snapshot")
        hits, strategy = await self._run(
            lambda: snapshot_hits(
                self._local_snapshot,
                query=question,
                limit=top_k,
                filters=filters,
            ),
            timeout,
        )
        recommendations = [
            project_snapshot_recommendation(hit) for hit in hits
        ]
        self._local_snapshot_metadata = snapshot_metadata(
            self._local_snapshot,
            hit_count=len(recommendations),
            strategy=strategy,
        )
        return recommendations

    async def search(self, command: AgentSearchCommand) -> AgentSearchOutcome:
        """Execute the shared search retrieval chain and return raw matches."""

        if command.top_k <= 0:
            raise ValueError("top_k must be positive")
        normalized_query = self._validated_query(command.query, field_name="query")
        filters = self._default_filters(command.filters)
        runtime_config = {"reranker_model": command.reranker_model}
        metadata: dict[str, Any] = {}
        started = self._clock()

        async def execute_search() -> AgentSearchOutcome:
            self._apply_index_state(metadata)
            try:
                with search_stage_metrics("search", "embedding"):
                    query_embedding = await self._run(
                        lambda: self._embedding_generator.embed(normalized_query),
                        self._stage_timeout("embedding"),
                    )
            except Exception as error:
                # The bounded keyword/literal chain in the match service remains
                # usable when the semantic model is unavailable. Preserve the
                # provider admission status for the explicit fallback envelope.
                metadata["semantic_unavailable"] = True
                metadata.update(semantic_failure_metadata(error))
                _annotate_semantic_failure(metadata)
                query_embedding = []

            try:
                with search_stage_metrics("search", "retrieval"):
                    results = await self._run(
                        lambda: self._vector_match_service.match(
                            query_embedding=query_embedding,
                            top_k=command.top_k,
                            query=normalized_query,
                            mode=command.mode,
                            min_score=command.min_score,
                            filters=filters,
                            runtime_config=runtime_config,
                        ),
                        self._stage_timeout("retrieval"),
                    )
            except Exception as error:
                # An admitted request must never disappear behind an opaque
                # transport 503 once the application has accepted it. Keep the
                # public envelope explicit and do not expose provider details.
                _mark_retrieval_unavailable(metadata, error)
                results = []

            config_applied = dict(runtime_config)
            config_applied["filters"] = filters
            # The delegate records retrieval degradation in runtime_config
            # because it is transport-independent. Publish it here as outcome
            # metadata so projection and cache policy can reject degraded data.
            metadata.update(config_applied.get("_retrieval", {}))
            if not results:
                # A dependency outage is not the only condition that would leave
                # a client with no actionable result. A healthy zero-hit query
                # also consumes the process snapshot before returning the
                # public empty envelope. That response is explicit degraded data,
                # never a synthetic normal result.
                results = await self._local_snapshot_search(
                    query=normalized_query,
                    top_k=command.top_k,
                    filters=filters,
                )
                if results:
                    metadata.update(self._local_snapshot_metadata)
            if not results:
                if metadata.get("retrieval_unavailable") is True:
                    _mark_terminal_search_fallback(metadata)
                else:
                    _mark_semantic_fallback(metadata, "search")

            self._publish_status(metadata)
            return AgentSearchOutcome(
                query=command.query,
                normalized_query=normalized_query,
                top_k=command.top_k,
                mode=command.mode,
                results=tuple(results or []),
                metadata=metadata,
                config_applied=config_applied,
                elapsed_ms=(self._clock() - started) * 1000,
            )

        def total_timeout_fallback(error_type: str) -> AgentSearchOutcome:
            return self.final_search_outcome(
                command,
                error_type=error_type,
                local_snapshot=self._local_snapshot,
            )

        return await self._cached_or_execute(
            "search",
            normalized_query=normalized_query,
            material={
                "top_k": command.top_k,
                "mode": command.mode,
                "min_score": command.min_score,
                "filters": filters,
                "reranker_model": command.reranker_model,
            },
            metadata=metadata,
            execute=execute_search,
            total_timeout_fallback=total_timeout_fallback,
        )

    @classmethod
    def final_recommendation_outcome(
        cls,
        command: AgentRecommendationCommand,
        *,
        error_type: str,
        local_snapshot: Any | None = None,
    ) -> AgentRecommendationOutcome:
        """Build the last-resort recommendation envelope from the snapshot."""

        metadata: dict[str, Any] = {
            "candidate_unavailable": True,
            "candidate_error": error_type,
        }
        recommendations: list[CandidateRecommendation] = []
        filters = default_search_filters(command.filters)
        try:
            if local_snapshot is not None:
                hits, strategy = snapshot_hits(
                    local_snapshot,
                    query=normalize_query(command.question),
                    limit=command.top_k,
                    filters=filters,
                )
                recommendations = [
                    project_snapshot_recommendation(hit) for hit in hits
                ]
                if recommendations:
                    metadata.update(
                        snapshot_metadata(
                            local_snapshot,
                            hit_count=len(recommendations),
                            strategy=strategy,
                        )
                    )
                    metadata["candidate_source"] = "local_snapshot"
                    metadata.pop("fallback_unavailable", None)
        except Exception:
            recommendations = []
        if not recommendations:
            metadata["fallback_unavailable"] = True
            metadata["fallback_reason_code"] = "CANDIDATE_RETRIEVAL_UNAVAILABLE"
            metadata["candidate_source"] = "fallback_unavailable"
            record_search_fallback_unavailable(
                "recommend", metadata["fallback_reason_code"]
            )
        else:
            metadata["candidate_source"] = "local_snapshot"
        cls._publish_status(metadata)
        response = CandidateRecommendationResponse(
            question=command.question,
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata=metadata,
            recommendations=recommendations,
            total_candidates=len(recommendations),
            selected_candidates=len(recommendations),
        )
        return AgentRecommendationOutcome(
            question=command.question,
            response=response,
            config_applied={
                "expand_factor": min(
                    command.expand_factor, MAX_RECOMMEND_EXPAND_FACTOR
                ),
                "reranker_model": (
                    None if not command.enable_rerank else command.reranker_model
                ),
            },
            elapsed_ms=0.0,
            diagnostics={
                "min_score_explicit": command.min_score_explicit,
                "vector_min_score": command.vector_min_score,
                "rerank_min_score": command.rerank_min_score,
            },
            metadata=dict(metadata),
        )

    async def recommend(
        self, command: AgentRecommendationCommand
    ) -> AgentRecommendationOutcome:
        """Run recommendation through the same normalized application boundary."""

        if command.top_k <= 0:
            raise ValueError("topK must be positive")
        if command.expand_factor <= 0:
            raise ValueError("expand_factor must be positive")
        if self._candidate_recommendation_service is None:
            raise ValueError("candidate_recommendation_service is required")
        normalized_question = self._validated_query(
            command.question, field_name="question"
        )
        filters = self._default_filters(command.filters)
        effective_expand_factor = min(command.expand_factor, MAX_RECOMMEND_EXPAND_FACTOR)
        runtime_config = {
            "expand_factor": effective_expand_factor,
            "reranker_model": None
            if not command.enable_rerank
            else command.reranker_model,
        }
        started = self._clock()
        metadata: dict[str, Any] = {}

        async def execute_recommend() -> AgentRecommendationOutcome:
            try:
                with search_stage_metrics("recommend", "retrieval"):
                    response = await self._run(
                        lambda: self._candidate_recommendation_service.recommend(
                            question=normalized_question,
                            mode=RetrievalMode.EXPERT_DIAGNOSIS,
                            participants=None,
                            max_candidates=command.top_k,
                            runtime_config=runtime_config,
                            filters=filters,
                            vector_min_score=command.vector_min_score,
                            rerank_min_score=command.rerank_min_score,
                        ),
                        self._stage_timeout("retrieval"),
                    )
            except Exception as error:
                response = CandidateRecommendationResponse(
                    question=normalized_question,
                    mode=RetrievalMode.EXPERT_DIAGNOSIS,
                    metadata=dict(
                        (runtime_config.get("_semantic_failure") or {})
                    ),
                )
                _mark_candidate_unavailable(response.metadata, type(error).__name__)

            self._apply_index_state(metadata)
            for key, value in metadata.items():
                if key in INDEX_STATE_FIELDS:
                    response.metadata.setdefault(key, value)
            _annotate_semantic_failure(response.metadata)
            if not response.recommendations:
                # Healthy zero-candidate outcomes use the same explicit snapshot
                # path as dependency failures. Recommendation clients therefore
                # receive an actionable degraded result whenever the snapshot has
                # a visible candidate, rather than an opaque empty success.
                local_recommendations = await self._local_snapshot_recommendations(
                    question=normalized_question,
                    top_k=command.top_k,
                    filters=filters,
                )
                if local_recommendations:
                    response.recommendations = local_recommendations
                    response.total_candidates = len(local_recommendations)
                    response.selected_candidates = len(local_recommendations)
                    response.metadata.update(self._local_snapshot_metadata)
                    response.metadata["candidate_source"] = "local_snapshot"

            if not response.recommendations:
                if response.metadata.get("semantic_unavailable") is True:
                    _mark_semantic_fallback(response.metadata, "recommend")
                    response.metadata["candidate_source"] = "fallback_unavailable"
                elif response.metadata.get("candidate_unavailable") is True:
                    _mark_terminal_recommendation_fallback(response.metadata)
                    response.metadata["candidate_source"] = "fallback_unavailable"
            self._publish_status(response.metadata)
            metadata["status"] = response.metadata["status"]
            metadata["reasons"] = response.metadata["reasons"]

            return AgentRecommendationOutcome(
                question=command.question,
                response=response,
                config_applied=dict(runtime_config),
                elapsed_ms=(self._clock() - started) * 1000,
                diagnostics={
                    "min_score_explicit": command.min_score_explicit,
                    "vector_min_score": command.vector_min_score,
                    "rerank_min_score": command.rerank_min_score,
                },
                metadata=metadata,
            )

        def total_timeout_fallback(error_type: str) -> AgentRecommendationOutcome:
            return self.final_recommendation_outcome(
                command,
                error_type=error_type,
                local_snapshot=self._local_snapshot,
            )

        return await self._cached_or_execute(
            "recommend",
            normalized_query=normalized_question,
            material={
                "top_k": command.top_k,
                "enable_rerank": command.enable_rerank,
                "reranker_model": command.reranker_model,
                "expand_factor": effective_expand_factor,
                "vector_min_score": command.vector_min_score,
                "rerank_min_score": command.rerank_min_score,
                "filters": filters,
            },
            metadata=metadata,
            execute=execute_recommend,
            total_timeout_fallback=total_timeout_fallback,
        )


__all__ = [
    "AgentRecommendationCommand",
    "AgentRecommendationOutcome",
    "AgentSearchCommand",
    "AgentSearchOutcome",
    "AgentSearchService",
    "MAX_RECOMMEND_EXPAND_FACTOR",
    "normalize_query",
]
