from typing import Protocol, List, Optional


class VectorStore(Protocol):
    """Public vector store contract.

    Implementations may be OSS defaults (Qdrant, FAISS) or internal plugins (ZDAS-backed).
    Public code must depend on this contract, not internal vector store SDKs.
    """

    def upsert(self, id: str, vector: List[float], metadata: dict = None) -> bool:
        """Insert or update a vector.

        Args:
            id: Unique vector identifier
            vector: Embedding vector
            metadata: Optional metadata dict

        Returns:
            True if operation successful, False otherwise.
        """
        ...

    def search(self, query_vector: List[float], top_k: int = 10, filter: dict = None) -> List[dict]:
        """Search for similar vectors.

        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return
            filter: Optional metadata filter

        Returns:
            List of search results with id, score, and metadata.
        """
        ...

    def get(self, id: str) -> Optional[dict]:
        """Get vector by ID.

        Args:
            id: Unique vector identifier

        Returns:
            Vector dict with id, vector, and metadata if found.
        """
        ...

    def delete(self, id: str) -> bool:
        """Delete a vector.

        Args:
            id: Unique vector identifier

        Returns:
            True if deletion successful, False otherwise.
        """
        ...

    def delete_by_filter(self, filter: dict) -> int:
        """Delete vectors matching filter.

        Args:
            filter: Metadata filter

        Returns:
            Number of vectors deleted.
        """
        ...