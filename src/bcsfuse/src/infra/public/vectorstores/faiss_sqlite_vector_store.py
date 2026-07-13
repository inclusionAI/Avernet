"""
FAISS SQLite Vector Store - OSS Wrapper

Wraps existing FAISS+SQLite implementation for OSS compatibility.
"""
import logging
from typing import List, Dict, Optional

from src.infra.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore as _FaissSqliteVectorStore
from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit

logger = logging.getLogger(__name__)


class FaissSqliteVectorStore(_FaissSqliteVectorStore):
    """
    FAISS SQLite Vector Store for OSS.

    This is a thin wrapper around the existing FAISS+SQLite implementation
    to maintain consistent naming and future extensibility.

    Suitable for development and single-instance deployments.
    For production, consider QdrantLocalVectorStore.
    """

    def upsert(self, id_or_points, vector: List[float] = None, metadata: Optional[Dict] = None) -> bool:
        """
        Insert or update vector(s).

        Supports two calling conventions for compatibility:
        1. Batch: upsert(points: list[VectorPoint]) — used by profile_embedding_store
        2. Single: upsert(id: str, vector: List[float], metadata: Optional[Dict]) — convenience

        Args:
            id_or_points: Either a list of VectorPoint objects (batch) or a string ID (single)
            vector: Vector data (only for single-mode)
            metadata: Optional metadata dict (only for single-mode)

        Returns:
            True if successful
        """
        # Batch mode: list[VectorPoint]
        if isinstance(id_or_points, list):
            super().upsert(id_or_points)
            return True

        # Single mode: (id, vector, metadata)
        point = VectorPoint(
            id=id_or_points,
            vector=vector,
            payload=metadata or {}
        )
        super().upsert([point])
        return True

    # ---- Post-filter support for Faiss (parity with Qdrant payload filtering) ----

    @staticmethod
    def _match_filters(payload: dict, filters: dict) -> bool:
        """
        Check if a vector's payload matches all filter conditions.

        Filter format (same as Qdrant-compatible filters used by
        worker_vector_match_service):
            {"runtime_state": ["online"], "availability": ["public", "protected"], "key": "value"}

        Rules:
        - list value → payload[key] must be IN the list (OR)
        - scalar value → payload[key] must EQUAL the value
        - key not in payload → SKIP (pass) — Faiss payloads may not contain
          all business metadata fields (test_id, business_regression) that
          Qdrant payloads do. Only reject when the key IS present but the
          value doesn't match.

        Args:
            payload: Vector payload metadata dict
            filters: Filter conditions dict

        Returns:
            True if payload matches all conditions
        """
        for key, condition in filters.items():
            if key not in payload:
                # Key absent in payload — can't filter, skip (pass)
                continue
            value = payload[key]
            if isinstance(condition, list):
                if value not in condition:
                    return False
            else:
                if value != condition:
                    return False
        return True

    def _post_filter_hits(
        self,
        hits: list[VectorSearchHit],
        filters: dict,
    ) -> list[VectorSearchHit]:
        """
        Apply post-filtering to search results.

        Faiss does not support payload filtering natively (unlike Qdrant),
        so we over-fetch and filter results by inspecting each hit's payload.

        Args:
            hits: Raw search results from Faiss
            filters: Filter conditions dict

        Returns:
            Filtered results
        """
        if not filters:
            return hits

        filtered = [h for h in hits if self._match_filters(h.payload, filters)]

        if len(filtered) == 0 and len(hits) > 0:
            # Diagnostic: show first hit's payload to help debug filter mismatches
            sample = hits[0]
            logger.warning(
                "[FaissSqliteVectorStore] Post-filter: 0/%d hits passed. "
                "filters=%s, sample_hit_id=%s, sample_payload_keys=%s",
                len(hits), list(filters.keys()), sample.id, list(sample.payload.keys())
            )

        return filtered

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        Vector similarity search with post-filtering.

        Faiss does not support payload filtering natively, so we:
        1. Over-fetch (top_k * 3) from Faiss
        2. Apply post-filter on payload
        3. Return at most top_k filtered results

        Args:
            vector: Query vector
            top_k: Maximum number of results
            filters: Optional filter conditions (e.g., {"runtime_state": ["online"]})

        Returns:
            Filtered search results sorted by similarity score
        """
        if not filters:
            return super().search(vector, top_k, filters=None)

        # Over-fetch to compensate for filtered-out results
        fetch_k = top_k * 3
        hits = super().search(vector, fetch_k, filters=None)

        filtered = self._post_filter_hits(hits, filters)

        return filtered[:top_k]

    def batch_search(
        self,
        vectors: list[list[float]],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        Batch vector similarity search with post-filtering.

        Args:
            vectors: Query vectors
            top_k: Maximum results per query
            filters: Optional filter conditions

        Returns:
            List of filtered result lists
        """
        if not filters:
            return super().batch_search(vectors, top_k, filters=None)

        fetch_k = top_k * 3
        batch_hits = super().batch_search(vectors, fetch_k, filters=None)

        return [
            self._post_filter_hits(hits, filters)[:top_k]
            for hits in batch_hits
        ]

    def stats(self) -> dict:
        """
        Get vector store statistics.

        Returns:
            Dictionary with stats including vector_count and indexed_workers
        """
        # Get vector count
        vector_count = self.size()

        # Get unique worker_ids from vectors
        indexed_workers = set()
        vector_ids = self.get_vector_ids()

        # For each vector, get its metadata from backend
        # Note: This requires reading from SQLite backend
        for vec_id in vector_ids:
            metadata = self._backend.get_metadata(vec_id)
            if metadata and "worker_id" in metadata:
                indexed_workers.add(metadata["worker_id"])

        return {
            "vector_count": vector_count,
            "dimension": self.dimension,
            "indexed_workers": len(indexed_workers),
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
        ids_to_delete = []

        # Find all vectors for this worker
        vector_ids = self.get_vector_ids()
        for vec_id in vector_ids:
            metadata = self._backend.get_metadata(vec_id)
            if metadata and metadata.get("worker_id") == worker_id:
                ids_to_delete.append(vec_id)

        # Delete vectors
        if ids_to_delete:
            self.delete(ids_to_delete)
            count = len(ids_to_delete)

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
        ids_to_delete = []

        # Find all vectors for this worker+profile
        vector_ids = self.get_vector_ids()
        for vec_id in vector_ids:
            metadata = self._backend.get_metadata(vec_id)
            if (metadata and
                metadata.get("worker_id") == worker_id and
                metadata.get("profile_id") == profile_id):
                ids_to_delete.append(vec_id)

        # Delete vectors
        if ids_to_delete:
            self.delete(ids_to_delete)
            count = len(ids_to_delete)

        return count