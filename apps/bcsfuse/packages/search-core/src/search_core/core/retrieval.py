"""Independent recall and ranking; strict by default, optional bounded degradation."""

from __future__ import annotations

import asyncio
import inspect
import math

from search_core.api.errors import SearchUnavailable
from search_core.api.models import (
    RecallResult,
    RetrievalRequest,
    ScoreDetails,
    SearchItem,
    SearchResult,
    StageScore,
)
from search_core.core.ranking import reciprocal_rank_fusion
from search_core.plugin_api.embedding import AsyncEmbeddingProvider, EmbeddingProvider
from search_core.plugin_api.execution import CallExecutor
from search_core.plugin_api.reranker import AsyncReranker, Reranker
from search_core.plugin_api.retrieval import RetrievalIndex


class _StageFailure(Exception):
    def __init__(self, reason):
        self.reason = reason


def _stage_scores(scores):
    # RRF counts each identity once. Keep the first occurrence and its order.
    unique = {}
    for key, value in scores:
        unique.setdefault(key, value)
    return {
        key: StageScore(value, rank)
        for rank, (key, value) in enumerate(unique.items(), 1)
        if math.isfinite(value)
    }


class HybridRetrievalService:
    """No business policy; recall/rank stages permit reusing one query embedding.

    TC retains strict failures by default. Partial results and execution pools
    are explicitly selected by a composition root. Hosts own pool shutdown.
    """

    def __init__(
        self,
        *,
        index: RetrievalIndex,
        embedding: EmbeddingProvider | AsyncEmbeddingProvider,
        reranker: Reranker | AsyncReranker | None = None,
        candidate_limit: int = 30,
        allow_partial: bool = False,
        execution: dict[str, CallExecutor] | None = None,
        keyword_enabled: bool = True,
        vector_enabled: bool = True,
        group_by_source: bool = False,
        recall_limit: int | None = None,
        vector_min_score: float | None = None,
        keyword_min_score: float | None = None,
    ):
        if not keyword_enabled and not vector_enabled:
            raise ValueError("at least one recall branch must be enabled")
        for threshold in (vector_min_score, keyword_min_score):
            if threshold is not None and (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold)
            ):
                raise ValueError("minimum scores must be finite numbers or None")
        self._thresholds = (vector_min_score, keyword_min_score)
        self._group_by_source = group_by_source
        self._recall_limit = candidate_limit if recall_limit is None else recall_limit
        if type(self._recall_limit) is not int or self._recall_limit < candidate_limit:
            raise ValueError("recall_limit must be at least candidate_limit")
        self._keyword_enabled = keyword_enabled
        self._vector_enabled = vector_enabled
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        self._index, self._embedding, self._reranker = index, embedding, reranker
        self._candidate_limit, self._allow_partial = candidate_limit, allow_partial
        self._execution = execution or {}

    async def _call(self, stage, function, *args):
        try:
            pool = self._execution.get(stage)
            if pool is None and stage in {"vector", "keyword"}:
                pool = self._execution.get("index")
            if pool is not None:
                return await pool.call(function, *args)
            if inspect.iscoroutinefunction(function):
                return await function(*args)
            return await asyncio.to_thread(function, *args)
        except Exception as exc:
            raise _StageFailure(stage + "_unavailable") from exc

    async def recall(self, request: RetrievalRequest) -> RecallResult:
        if not request.query.strip() or not 0 < request.limit <= self._candidate_limit:
            raise ValueError("query and limit within candidate_limit are required")
        if request.allowed_ids == frozenset() or any(
            not v for v in request.filters.values()
        ):
            return RecallResult()
        query_vector = ()

        async def semantic():
            nonlocal query_vector
            if not self._vector_enabled:
                return None
            vector = await self._call("embedding", self._embedding.embed, request.query)
            if not vector or any(not math.isfinite(v) for v in vector):
                raise _StageFailure("embedding_unavailable")
            query_vector = tuple(vector)
            return await self._call(
                "vector",
                self._index.vector_search,
                vector,
                self._recall_limit,
                request.filters,
                request.allowed_ids,
            )

        async def keyword():
            if not self._keyword_enabled:
                return None
            return await self._call(
                "keyword",
                self._index.keyword_search,
                request.query,
                self._recall_limit,
                request.filters,
                request.allowed_ids,
            )

        results = await asyncio.gather(
            semantic(),
            keyword(),
            return_exceptions=True,
        )
        reasons, branches = [], []
        available = False
        for position, result in enumerate(results):
            if not (self._vector_enabled, self._keyword_enabled)[position]:
                reasons.append(
                    "vector_disabled" if position == 0 else "keyword_disabled"
                )
                branches.append(())
                continue
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                if not self._allow_partial:
                    raise SearchUnavailable("retrieval dependency failed") from result
                reason = (
                    result.reason
                    if isinstance(result, _StageFailure)
                    else (
                        "vector_unavailable" if position == 0 else "keyword_unavailable"
                    )
                )
                if reason == "index_unavailable":
                    reason = (
                        "vector_unavailable" if position == 0 else "keyword_unavailable"
                    )
                reasons.append(reason)
                branches.append(())
            else:
                available = True
                hits = tuple(result)
                if request.allowed_ids is not None and any(
                    self._identity(h) not in request.allowed_ids for h in hits
                ):
                    raise SearchUnavailable("index returned an unauthorized candidate")
                threshold = self._thresholds[position]
                if threshold is not None:
                    hits = tuple(
                        h
                        for h in hits
                        if math.isfinite(h.score) and h.score >= threshold
                    )
                if self._group_by_source:
                    unique = {}
                    for h in sorted(hits, key=lambda h: (-h.score, h.id)):
                        source = self._identity(h)
                        unique.setdefault(source, h.model_copy(update={"id": source}))
                    hits = tuple(unique.values())
                branches.append(hits)
        return RecallResult(
            branches[0],
            branches[1],
            query_vector,
            tuple(dict.fromkeys(reasons)),
            available,
        )

    def _identity(self, hit):
        if not self._group_by_source:
            return hit.id
        source = hit.payload.get("source_id")
        if not isinstance(source, str) or not source:
            raise SearchUnavailable("document source identity missing")
        return source

    async def rank(
        self, request: RetrievalRequest, recall: RecallResult
    ) -> SearchResult:
        if not recall.available:
            raise SearchUnavailable("all recall paths unavailable")
        hits = {h.id: h for h in [*recall.keyword_hits, *recall.vector_hits]}
        ranking = reciprocal_rank_fusion(
            [[h.id for h in recall.vector_hits], [h.id for h in recall.keyword_hits]]
        )[: self._candidate_limit]
        scores = [(r.item_id, r.score) for r in ranking]
        keyword_scores = _stage_scores((h.id, h.score) for h in recall.keyword_hits)
        vector_scores = _stage_scores((h.id, h.score) for h in recall.vector_hits)
        fusion_scores = _stage_scores(scores)
        reranker_scores = {}
        reasons = list(recall.reasons)
        score_source = "rrf"
        if self._reranker is not None and ranking:
            try:
                reranked = await self._call(
                    "reranker",
                    self._reranker.rerank,
                    request.query,
                    [
                        {
                            "id": r.item_id,
                            "text": str(
                                hits[r.item_id].payload.get("rerank_text")
                                or hits[r.item_id].payload.get("search_text")
                                or hits[r.item_id].payload.get("content")
                                or ""
                            ),
                        }
                        for r in ranking
                    ],
                    request.limit,
                )
                ids = [r.candidate_id for r in reranked]
                if len(ids) != len(set(ids)) or not set(ids) <= {
                    r.item_id for r in ranking
                }:
                    raise ValueError("invalid reranker candidate identities")
                if any(not math.isfinite(r.score) for r in reranked):
                    raise ValueError("non-finite reranker score")
                scores = [(r.candidate_id, r.score) for r in reranked]
                reranker_scores = _stage_scores(scores)
                score_source = "reranker"
            except Exception as exc:
                if not self._allow_partial:
                    raise SearchUnavailable("reranker failed") from exc
                reasons.append("reranker_unavailable")
        return SearchResult(
            items=tuple(
                SearchItem(
                    item_id=key,
                    name=str(hits[key].payload.get("name") or ""),
                    summary=str(hits[key].payload.get("content") or ""),
                    score=score,
                    score_source=score_source,
                    score_details=ScoreDetails(
                        keyword=keyword_scores.get(key),
                        vector=vector_scores.get(key),
                        rrf=fusion_scores.get(key),
                        reranker=reranker_scores.get(key),
                    ),
                )
                for key, score in scores[: request.limit]
            ),
            status="degraded" if reasons else "ok",
            reasons=tuple(dict.fromkeys(reasons)),
        )

    async def retrieve(self, request: RetrievalRequest) -> SearchResult:
        return await self.rank(request, await self.recall(request))
