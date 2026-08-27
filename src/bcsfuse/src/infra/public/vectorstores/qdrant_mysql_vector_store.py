"""
Qdrant + MySQL Vector Store (OSS production durable backend)

Aligns with the internal QdrantZdasVectorStore interface and the
VectorStoreAdapter protocol.

Architecture:
- MySQL (bcsfuse_vector_points): durable source of truth
- Qdrant Local: disposable local index for fast ANN search
- Write-through: MySQL first, then Qdrant
- Rebuild: load all vectors from MySQL into Qdrant on startup/request
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, List, Optional

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore
from src.infra.vectorstore_backends.mysql_vector_persistence_backend import MySQLVectorPersistenceBackend

logger = logging.getLogger(__name__)


class QdrantMySQLVectorStore(VectorStoreAdapter):
    """MySQL-backed durable vector store with local Qdrant index."""

    def __init__(
        self,
        collection_name: str = "bcsfuse_profiles",
        qdrant_path: Optional[str] = None,
        dimension: int = 4096,
        distance: str = "Cosine",
        mysql_host: Optional[str] = None,
        mysql_port: Optional[int] = None,
        mysql_user: Optional[str] = None,
        mysql_password: Optional[str] = None,
        mysql_database: Optional[str] = None,
    ):
        self.collection_name = collection_name
        self.dimension = dimension
        self.distance = distance

        self._qdrant = QdrantLocalVectorStore(
            collection_name=collection_name,
            path=qdrant_path,
            dimension=dimension,
            distance=distance,
        )

        self._mysql = MySQLVectorPersistenceBackend(
            host=mysql_host,
            port=mysql_port,
            user=mysql_user,
            password=mysql_password,
            database=mysql_database,
            collection_name=collection_name,
            vector_dimension=dimension,
            distance_metric=distance,
        )

        logger.info(
            "[QdrantMySQLVectorStore] Initialized collection=%s dimension=%d distance=%s",
            collection_name, dimension, distance,
        )

    # ------------------------------------------------------------------
    # Compatibility proxies for callers that reach into Qdrant internals
    # ------------------------------------------------------------------

    def _ensure_client(self) -> None:
        """Proxy to underlying Qdrant local store (for route-layer compatibility)."""
        self._qdrant._ensure_client()

    @property
    def _client(self):
        """Proxy to underlying Qdrant client (for route-layer compatibility)."""
        return self._qdrant._client

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def upsert(self, points: List[VectorPoint]) -> None:
        """Insert or update vector points.

        Write-through: durable MySQL first, then local Qdrant index.
        Qdrant index failures are logged but not raised (index can be rebuilt).
        """
        if not points:
            return

        # 1. Durable write to MySQL (must succeed)
        self._mysql.save_batch(points)

        # 2. Update local Qdrant index
        try:
            for point in points:
                self._qdrant._upsert_one(point.id, point.vector, point.payload)
        except Exception as e:
            logger.warning(
                "[QdrantMySQLVectorStore] Qdrant index update failed (MySQL is safe): %s", e
            )

    def delete(self, ids: List[str]) -> None:
        """Delete vectors by business IDs."""
        if not ids:
            return

        # 1. Delete from MySQL
        self._mysql.delete_batch(ids)

        # 2. Delete from local Qdrant
        try:
            for id in ids:
                self._qdrant.delete(id)
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Qdrant delete failed: %s", e)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        vector: List[float],
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[VectorSearchHit]:
        """Search local Qdrant index and return VectorSearchHit list.

        The underlying QdrantLocalVectorStore.search() already returns
        VectorSearchHit objects (with .id/.score/.payload attributes), so we
        pass them through directly. Do NOT re-wrap them via dict .get() -- the
        results are objects, not dicts.
        """
        return self._qdrant.search(vector, top_k, filter=filters)

    def batch_search(
        self,
        vectors: List[List[float]],
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[List[VectorSearchHit]]:
        """Batch search (sequential)."""
        return [self.search(v, top_k, filters) for v in vectors]

    # ------------------------------------------------------------------
    # Misc protocol methods
    # ------------------------------------------------------------------

    def size(self) -> int:
        return self._qdrant.size()

    def count(self) -> int:
        return self.size()

    def get_vector_ids(self) -> List[str]:
        return self._qdrant.get_vector_ids()

    def get(self, id: str) -> Optional[VectorPoint]:
        """Get a single vector point from MySQL durable backend."""
        for point in self._mysql.load_all():
            if point.id == id:
                return point
        return None

    def save_snapshot(self, path: str) -> None:
        """Not implemented for MySQL backend (data is already durable)."""
        logger.warning("[QdrantMySQLVectorStore] save_snapshot not implemented")

    def load_snapshot(self, path: str) -> None:
        """Not implemented for MySQL backend."""
        logger.warning("[QdrantMySQLVectorStore] load_snapshot not implemented")

    def text_search(
        self,
        query: str,
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[VectorSearchHit]:
        """Text-based search is delegated to Qdrant local (if it supports it)."""
        # QdrantLocalVectorStore does not expose text_search; fall back to empty.
        logger.warning("[QdrantMySQLVectorStore] text_search not implemented")
        return []

    def batch_text_search(
        self,
        queries: List[str],
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[List[VectorSearchHit]]:
        logger.warning("[QdrantMySQLVectorStore] batch_text_search not implemented")
        return [[] for _ in queries]

    # ------------------------------------------------------------------
    # Rebuild from MySQL
    # ------------------------------------------------------------------

    def rebuild_from_mysql(self, batch_size: int = 100) -> dict[str, Any]:
        """Rebuild local Qdrant index from MySQL durable backend."""
        logger.info("[QdrantMySQLVectorStore] Rebuilding Qdrant index from MySQL...")
        start = time.time()

        # Clear local Qdrant index
        try:
            self._qdrant.clear()
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to clear Qdrant index: %s", e)

        total_loaded = 0
        total_indexed = 0

        all_points = self._mysql.load_all()

        for i in range(0, len(all_points), batch_size):
            points = all_points[i:i + batch_size]
            total_loaded += len(points)

            try:
                self._qdrant.upsert(points)
                total_indexed += len(points)
            except Exception as e:
                logger.error(
                    "[QdrantMySQLVectorStore] Failed to index batch offset=%d: %s",
                    i, e,
                )
                raise

        duration_ms = (time.time() - start) * 1000
        result = {
            "loaded_count": total_loaded,
            "indexed_count": total_indexed,
            "qdrant_size": self._qdrant.size(),
            "duration_ms": duration_ms,
        }
        logger.info(
            "[QdrantMySQLVectorStore] Rebuild complete: loaded=%d indexed=%d qdrant_size=%d duration_ms=%.2f",
            result["loaded_count"], result["indexed_count"], result["qdrant_size"], duration_ms,
        )
        return result

    def clear(self) -> None:
        """Clear both Qdrant and MySQL data. Use with caution."""
        try:
            self._qdrant.clear()
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to clear Qdrant: %s", e)
        try:
            ids = self._mysql.load_all()
            if ids:
                self._mysql.delete_batch([p.id for p in ids])
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to clear MySQL: %s", e)

    def close(self) -> None:
        try:
            self._mysql.close()
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to close MySQL: %s", e)


__all__ = ["QdrantMySQLVectorStore"]
