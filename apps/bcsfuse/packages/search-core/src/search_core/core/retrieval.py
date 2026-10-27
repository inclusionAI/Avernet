"""Independent lexical/semantic recall, rank fusion and optional model reranking."""

from __future__ import annotations

import asyncio
import math

from search_core.api.errors import SearchUnavailable
from search_core.api.models import RetrievalRequest, SearchItem, SearchResult
from search_core.core.ranking import reciprocal_rank_fusion
from search_core.plugin_api.embedding import EmbeddingProvider
from search_core.plugin_api.reranker import Reranker
from search_core.plugin_api.retrieval import RetrievalIndex


class HybridRetrievalService:
    """No Worker/Profile policy, HTTP transport, credentials or silent fallback.

    Synchronous providers run off the event loop. Keyword recall can proceed
    while Embedding is in flight; model/backend failures propagate to the app.
    """

    def __init__(
        self,
        *,
        index: RetrievalIndex,
        embedding: EmbeddingProvider,
        reranker: Reranker | None = None,
        candidate_limit: int = 30,
    ):
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        self._index = index
        self._embedding = embedding
        self._reranker = reranker
        self._candidate_limit = candidate_limit

    async def retrieve(self, request: RetrievalRequest) -> SearchResult:
        if not request.query.strip() or not 0 < request.limit <= self._candidate_limit:
            raise ValueError("query and limit within candidate_limit are required")
        if request.allowed_ids == frozenset() or any(
            not v for v in request.filters.values()
        ):
            return SearchResult()

        def semantic():
            vector = self._embedding.embed(request.query)
            return self._index.vector_search(
                vector,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            )

        results = await asyncio.gather(
            asyncio.to_thread(semantic),
            asyncio.to_thread(
                self._index.keyword_search,
                request.query,
                self._candidate_limit,
                request.filters,
                request.allowed_ids,
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise SearchUnavailable("retrieval dependency failed") from result
        vector_hits, keyword_hits = results
        hits = {hit.id: hit for hit in [*keyword_hits, *vector_hits]}
        if request.allowed_ids is not None and not hits.keys() <= request.allowed_ids:
            raise SearchUnavailable("index returned an unauthorized candidate")
        ranking = reciprocal_rank_fusion(
            [[h.id for h in vector_hits], [h.id for h in keyword_hits]]
        )
        candidates = ranking[: self._candidate_limit]
        scores = [(r.item_id, r.score) for r in candidates]
        score_source = "rrf"
        if self._reranker is not None and candidates:
            try:
                reranked = await asyncio.to_thread(
                    self._reranker.rerank,
                    request.query,
                    [
                        {
                            "id": r.item_id,
                            "text": str(hits[r.item_id].payload.get("content") or ""),
                        }
                        for r in candidates
                    ],
                    request.limit,
                )
                expected = {r.item_id for r in candidates}
                ids = [r.candidate_id for r in reranked]
                if len(ids) != len(set(ids)) or not set(ids) <= expected:
                    raise ValueError("invalid reranker candidate identities")
                if any(not math.isfinite(r.score) for r in reranked):
                    raise ValueError("non-finite reranker score")
                scores = [(r.candidate_id, r.score) for r in reranked]
                score_source = "reranker"
            except Exception as exc:
                raise SearchUnavailable("reranker failed") from exc
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
            )
        )
