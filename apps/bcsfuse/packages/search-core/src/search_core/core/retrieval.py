"""Independent recall and ranking with bounded execution and visible fallback."""

from __future__ import annotations

import asyncio
import math

from search_core.api.errors import SearchUnavailable
from search_core.api.models import (
    RecallResult,
    RetrievalRequest,
    SearchItem,
    SearchResult,
)
from search_core.core.ranking import reciprocal_rank_fusion
from search_core.plugin_api.embedding import EmbeddingProvider
from search_core.plugin_api.execution import CallExecutor
from search_core.plugin_api.reranker import Reranker
from search_core.plugin_api.retrieval import RetrievalIndex


class _StageFailure(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class HybridRetrievalService:
    """Run recall branches independently and rank once from staged results.

    Synchronous providers run off the event loop. A staged execution pool can
    bound physical calls. One branch failure is visible degradation when the
    other branch succeeds. If both fail, a configured literal fallback may be
    used; otherwise retrieval is unavailable and never an empty success.
    """

    def __init__(
        self,
        *,
        index: RetrievalIndex,
        embedding: EmbeddingProvider,
        reranker: Reranker | None = None,
        candidate_limit: int = 30,
        fallback_index: RetrievalIndex | None = None,
        allow_partial: bool = False,
        execution: dict[str, CallExecutor] | None = None,
    ):
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        self._index = index
        self._embedding = embedding
        self._reranker = reranker
        self._candidate_limit = candidate_limit
        self._fallback_index = fallback_index
        self._allow_partial = allow_partial
        self._execution = dict(execution or {})

    async def _call(self, stage: str, function, *args):
        pool = self._execution.get(stage)
        if pool is not None:
            return await pool.call(function, *args)
        return await asyncio.to_thread(function, *args)

    async def recall(self, request: RetrievalRequest) -> RecallResult:
        if not request.query.strip() or not 0 < request.limit <= self._candidate_limit:
            raise ValueError("query and limit within candidate_limit are required")
        if request.allowed_ids == frozenset() or any(
            not values for values in request.filters.values()
        ):
            return RecallResult()

        query_vector: tuple[float, ...] = ()

        async def semantic():
            nonlocal query_vector
            try:
                vector = await self._call("embedding", self._embedding.embed, request.query)
            except Exception as exc:
                raise _StageFailure("semantic_unavailable") from exc
            if not vector or any(not math.isfinite(value) for value in vector):
                raise _StageFailure("semantic_unavailable")
            query_vector = tuple(vector)
            try:
                return await self._call(
                    "index",
                    self._index.vector_search,
                    vector,
                    self._candidate_limit,
                    request.filters,
                    request.allowed_ids,
                )
            except Exception as exc:
                raise _StageFailure("semantic_unavailable") from exc

        async def keyword():
            try:
                return await self._call(
                    "index",
                    self._index.keyword_search,
                    request.query,
                    self._candidate_limit,
                    request.filters,
                    request.allowed_ids,
                )
            except Exception as exc:
                raise _StageFailure("keyword_unavailable") from exc

        results = await asyncio.gather(
            semantic(),
            keyword(),
            return_exceptions=True,
        )
        vector_result = self._stage_result(results[0], "vector_unavailable")
        keyword_result = self._stage_result(results[1], "keyword_unavailable")
        vector_failure = _exception_from(vector_result)
        keyword_failure = _exception_from(keyword_result)

        if vector_failure is not None and keyword_failure is not None:
            if not self._allow_partial:
                raise SearchUnavailable("retrieval dependency failed") from keyword_failure
            return RecallResult(
                reasons=("semantic_unavailable", "keyword_unavailable"),
                available=False,
            )

        vector_hits: tuple = () if vector_failure is not None else tuple(vector_result)
        keyword_hits: tuple = () if keyword_failure is not None else tuple(keyword_result)
        reasons = []
        if vector_failure is not None:
            reasons.append(getattr(vector_failure, "reason", "vector_unavailable"))
        if keyword_failure is not None:
            reasons.append(getattr(keyword_failure, "reason", "keyword_unavailable"))

        hits = {hit.id: hit for hit in [*keyword_hits, *vector_hits]}
        _validate_authorization(hits, request.allowed_ids)
        return RecallResult(
            vector_hits=vector_hits,
            keyword_hits=keyword_hits,
            query_vector=query_vector,
            reasons=tuple(dict.fromkeys(reasons)),
            available=True,
        )

    async def rank(self, request: RetrievalRequest, recall: RecallResult) -> SearchResult:
        if not recall.available:
            raise SearchUnavailable("all recall paths unavailable")
        hits = {hit.id: hit for hit in [*recall.keyword_hits, *recall.vector_hits]}
        ranking = reciprocal_rank_fusion(
            [[hit.id for hit in recall.vector_hits], [hit.id for hit in recall.keyword_hits]]
        )[: self._candidate_limit]
        scores = [(item.item_id, item.score) for item in ranking]
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
                            "id": item.item_id,
                            "text": str(hits[item.item_id].payload.get("content") or ""),
                        }
                        for item in ranking
                    ],
                    request.limit,
                )
                expected = {item.item_id for item in ranking}
                ids = [item.candidate_id for item in reranked]
                if len(ids) != len(set(ids)) or not set(ids) <= expected:
                    raise ValueError("invalid reranker candidate identities")
                if any(not math.isfinite(item.score) for item in reranked):
                    raise ValueError("non-finite reranker score")
                scores = [(item.candidate_id, item.score) for item in reranked]
                score_source = "reranker"
            except ValueError as exc:
                # Malformed model output can never be interpreted as an outage.
                raise SearchUnavailable("invalid reranker response") from exc
            except Exception:
                reasons.append("reranker_unavailable")

        return SearchResult(
            items=_items(hits, scores[: request.limit], score_source),
            status="degraded" if reasons else "ok",
            reasons=tuple(dict.fromkeys(reasons)),
        )

    async def retrieve(self, request: RetrievalRequest) -> SearchResult:
        try:
            recall = await self.recall(request)
        except SearchUnavailable as exc:
            if self._fallback_index is None:
                raise
            return await self._regex_fallback(request, exc)
        try:
            return await self.rank(request, recall)
        except SearchUnavailable as exc:
            if recall.available:
                raise
            if self._fallback_index is None:
                raise
            return await self._regex_fallback(request, exc)

    async def _regex_fallback(
        self,
        request: RetrievalRequest,
        failure: Exception,
    ) -> SearchResult:
        if self._fallback_index is None:
            raise SearchUnavailable("retrieval dependency failed") from failure
        try:
            fallback_hits = await self._call(
                "fallback",
                self._fallback_index.keyword_search,
                request.query,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            )
        except Exception as exc:
            raise SearchUnavailable("regex fallback failed") from exc
        hits = {hit.id: hit for hit in fallback_hits}
        _validate_authorization(hits, request.allowed_ids)
        ranking = reciprocal_rank_fusion([[hit.id for hit in fallback_hits]])
        scores = [(item.item_id, item.score) for item in ranking]
        return SearchResult(
            items=_items(hits, scores[: request.limit], "regex_fallback"),
            status="degraded",
            reasons=(
                "semantic_unavailable",
                "keyword_unavailable",
                "regex_fallback",
            ),
        )

    def _stage_result(self, result: object, fallback_reason: str):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            reason = getattr(result, "reason", fallback_reason)
            return _StageFailure(reason)
        return result


def _exception_from(result: object) -> Exception | None:
    return result if isinstance(result, Exception) else None


def _validate_authorization(hits, allowed_ids) -> None:
    if allowed_ids is not None and not hits.keys() <= allowed_ids:
        raise SearchUnavailable("index returned an unauthorized candidate")


def _items(hits, scores, score_source):
    return tuple(
        SearchItem(
            item_id=item_id,
            name=str(hits[item_id].payload.get("name") or ""),
            summary=str(hits[item_id].payload.get("content") or ""),
            score=score,
            score_source=score_source,
        )
        for item_id, score in scores
    )
