"""
Noop Reranker - For Testing

A no-op reranker that returns documents as-is without scoring.
"""
from typing import List
from src.domain.services.reranker import Reranker, RerankResult


class NoopReranker(Reranker):
    """
    No-op Reranker for testing.

    Returns documents in original order without any scoring.
    Useful for testing and development without external API dependencies.
    DO NOT use in production.
    """

    def __init__(self):
        """Initialize no-op reranker."""
        pass

    def rerank(
        self, query: str, candidates: List[dict], top_k: int = 5
    ) -> List[RerankResult]:
        """
        Return candidates in original order without scoring.

        Args:
            query: The query string (ignored).
            candidates: List of candidate documents.
            top_k: Number of results to return.

        Returns:
            List of RerankResult with default score of 1.0.
        """
        results = []
        for i, candidate in enumerate(candidates[:top_k]):
            results.append(
                RerankResult(
                    document=candidate,
                    score=1.0,
                    rank=i + 1,
                )
            )
        return results