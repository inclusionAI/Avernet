from typing import Protocol, List


class RerankerProvider(Protocol):
    """Public reranker provider contract.

    Implementations may be OSS defaults or internal plugins.
    Public code must depend on this contract, not internal reranker SDKs.
    """

    def rerank(self, query: str, documents: List[dict], top_k: int = None) -> List[dict]:
        """Rerank documents based on query relevance.

        Args:
            query: Query string
            documents: List of document dicts with 'id', 'content', and optional 'metadata'
            top_k: Number of top results to return (None for all)

        Returns:
            List of reranked documents with 'id', 'score', and 'content'.
        """
        ...

    def score(self, query: str, documents: List[dict]) -> List[float]:
        """Score documents for query relevance.

        Args:
            query: Query string
            documents: List of document dicts with 'content'

        Returns:
            List of relevance scores (0.0 to 1.0).
        """
        ...