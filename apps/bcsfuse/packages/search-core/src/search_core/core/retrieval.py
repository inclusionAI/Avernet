"""Independent recall and ranking; strict by default, optional bounded degradation."""

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
    def __init__(self, reason):
        self.reason = reason


class HybridRetrievalService:
    """No business policy; recall/rank stages permit reusing one query embedding.

    TC retains strict failures by default. Partial results and execution pools
    are explicitly selected by a composition root. Hosts own pool shutdown.
    """

    def __init__(
        self,
        *,
        index: RetrievalIndex,
        embedding: EmbeddingProvider,
        reranker: Reranker | None = None,
        candidate_limit: int = 30,
        allow_partial: bool = False,
        execution: dict[str, CallExecutor] | None = None,
    ):
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        self._index, self._embedding, self._reranker = index, embedding, reranker
        self._candidate_limit, self._allow_partial = candidate_limit, allow_partial
        self._execution = execution or {}

    async def _call(self, stage, function, *args):
        try:
            pool = self._execution.get(stage)
            if pool is not None:
                return await pool.call(function, *args)
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
            vector = await self._call("embedding", self._embedding.embed, request.query)
            if not vector or any(not math.isfinite(v) for v in vector):
                raise _StageFailure("embedding_unavailable")
            query_vector = tuple(vector)
            return await self._call(
                "index",
                self._index.vector_search,
                vector,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            )

        results = await asyncio.gather(
            semantic(),
            self._call(
                "index",
                self._index.keyword_search,
                request.query,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            ),
            return_exceptions=True,
        )
        reasons, branches = [], []
        for position, result in enumerate(results):
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
                hits = tuple(result)
                if request.allowed_ids is not None and any(
                    h.id not in request.allowed_ids for h in hits
                ):
                    raise SearchUnavailable("index returned an unauthorized candidate")
                branches.append(hits)
        return RecallResult(
            branches[0],
            branches[1],
            query_vector,
            tuple(dict.fromkeys(reasons)),
            len(reasons) < 2,
        )

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
                            "text": str(hits[r.item_id].payload.get("content") or ""),
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
                )
                for key, score in scores[: request.limit]
            ),
            status="degraded" if reasons else "ok",
            reasons=tuple(dict.fromkeys(reasons)),
        )

    async def retrieve(self, request: RetrievalRequest) -> SearchResult:
        return await self.rank(request, await self.recall(request))
