"""
In-Memory Vector Store

Simple in-memory vector store implementation for testing.
"""
from typing import Optional, List, Dict
import numpy as np


class InMemoryVectorStore:
    """
    In-Memory Vector Store for OSS testing.

    Simple implementation using numpy for vector operations.
    Suitable for testing only. DO NOT use in production.
    Data is NOT persisted and is lost on restart.
    """

    def __init__(self, dimension: int = 4096):
        """Initialize in-memory vector store.

        Args:
            dimension: Vector dimension (default: 4096).
        """
        self.dimension = dimension
        self._vectors: dict[str, np.ndarray] = {}
        self._metadata: dict[str, dict] = {}

    def upsert(self, id: str, vector: List[float], metadata: Optional[Dict] = None) -> bool:
        """Insert or update a vector.

        Args:
            id: Vector ID.
            vector: Vector data.
            metadata: Optional metadata.

        Returns:
            True if successful.
        """
        if len(vector) != self.dimension:
            raise ValueError(f"Vector dimension mismatch: expected {self.dimension}, got {len(vector)}")

        self._vectors[id] = np.array(vector, dtype=np.float32)
        self._metadata[id] = metadata or {}
        return True

    def search(self, query_vector: List[float], top_k: int = 10, filter: Optional[Dict] = None) -> List[Dict]:
        """Search for similar vectors.

        Args:
            query_vector: Query vector.
            top_k: Number of results to return.
            filter: Optional filter (not implemented).

        Returns:
            List of search results with id, score, and metadata.
        """
        if len(query_vector) != self.dimension:
            raise ValueError(f"Vector dimension mismatch: expected {self.dimension}, got {len(query_vector)}")

        if not self._vectors:
            return []

        query = np.array(query_vector, dtype=np.float32)

        # Calculate cosine similarity
        scores = []
        for id, vec in self._vectors.items():
            similarity = np.dot(query, vec) / (np.linalg.norm(query) * np.linalg.norm(vec))
            scores.append((id, similarity))

        # Sort by similarity (descending)
        scores.sort(key=lambda x: x[1], reverse=True)

        # Return top_k results
        results = []
        for id, score in scores[:top_k]:
            results.append({
                "id": id,
                "score": float(score),
                "metadata": self._metadata.get(id, {}),
            })

        return results

    def get(self, id: str) -> Optional[Dict]:
        """Get vector by ID.

        Args:
            id: Vector ID.

        Returns:
            Vector data with metadata, or None if not found.
        """
        if id not in self._vectors:
            return None

        return {
            "id": id,
            "vector": self._vectors[id].tolist(),
            "metadata": self._metadata.get(id, {}),
        }

    def delete(self, id: str) -> bool:
        """Delete vector by ID.

        Args:
            id: Vector ID.

        Returns:
            True if deleted, False if not found.
        """
        if id in self._vectors:
            del self._vectors[id]
            del self._metadata[id]
            return True
        return False

    def delete_by_filter(self, filter: Dict) -> int:
        """Delete vectors by filter (not implemented).

        Args:
            filter: Filter criteria.

        Returns:
            Number of vectors deleted.
        """
        # TODO: Implement filter-based deletion
        return 0

    def size(self) -> int:
        """Get number of vectors in store.

        Returns:
            Number of vectors.
        """
        return len(self._vectors)

    def clear(self) -> None:
        """Clear all vectors."""
        self._vectors.clear()
        self._metadata.clear()

    def stats(self) -> dict:
        """
        Get vector store statistics.

        Returns:
            Dictionary with stats including vector_count
        """
        return {
            "vector_count": len(self._vectors),
            "dimension": self.dimension,
        }

    def delete_by_worker(self, worker_id: str) -> int:
        """
        Delete all vectors for a worker.

        Args:
            worker_id: Worker ID

        Returns:
            Number of vectors deleted
        """
        count = 0
        keys_to_delete = []

        for vec_id, metadata in self._metadata.items():
            if metadata.get("worker_id") == worker_id:
                keys_to_delete.append(vec_id)

        for key in keys_to_delete:
            self.delete(key)
            count += 1

        return count

    def delete_by_profile(self, worker_id: str, profile_id: str) -> int:
        """
        Delete all vectors for a specific profile.

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            Number of vectors deleted
        """
        count = 0
        keys_to_delete = []

        for vec_id, metadata in self._metadata.items():
            if metadata.get("worker_id") == worker_id and metadata.get("profile_id") == profile_id:
                keys_to_delete.append(vec_id)

        for key in keys_to_delete:
            self.delete(key)
            count += 1

        return count