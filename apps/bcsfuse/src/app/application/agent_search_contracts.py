"""Transport-independent commands and outcomes for AgentSearchService."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.models.candidate_recommendation import (
    CandidateRecommendationResponse,
)
from app.application.services.query_normalizer import normalize_query_text


def default_search_filters(
    filters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge request filters with the v1 search and recommendation defaults."""

    merged: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        # Copy mutable filter arrays without corrupting scalar aliases such
        # as worker_id: ``list("bot:owner")`` must not become a character list.
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


async def default_offload(operation: Callable[[], Any]) -> Any:
    """Run synchronous search work away from the event loop by default."""

    return await asyncio.to_thread(operation)


def normalize_query(query: str) -> str:
    """Return the stable cache/retrieval representation."""

    return normalize_query_text(query)


__all__ = [
    "AgentRecommendationCommand",
    "AgentRecommendationOutcome",
    "AgentSearchCommand",
    "AgentSearchOutcome",
    "MAX_RECOMMEND_EXPAND_FACTOR",
    "Offload",
    "default_offload",
    "default_search_filters",
    "normalize_query",
]
