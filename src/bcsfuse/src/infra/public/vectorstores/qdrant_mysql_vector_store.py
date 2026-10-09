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

import logging
import threading
import time
from typing import Any, List, Optional

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.domain.services.vector_persistence_backend import (
    IncrementalVectorPersistenceBackend,
    VectorPersistenceBackend,
)
from src.infra.public.observability.storage_logging import (
    log_storage_error,
    log_storage_event,
)
from src.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore
from src.infra.vectorstore_backends.mysql_vector_persistence_backend import MySQLVectorPersistenceBackend

logger = logging.getLogger(__name__)


class QdrantMySQLVectorStore(VectorStoreAdapter):
    """Write-through durable vector store with a local Qdrant index.

    MySQL remains the default durable backend. Internal compositions may inject
    another implementation of the same persistence contract.
    """

    def __init__(
        self,
        collection_name: str = "bcsfuse_vectors",
        qdrant_path: Optional[str] = None,
        dimension: int = 4096,
        distance: str = "Cosine",
        mysql_host: Optional[str] = None,
        mysql_port: Optional[int] = None,
        mysql_user: Optional[str] = None,
        mysql_password: Optional[str] = None,
        mysql_database: Optional[str] = None,
        persistence_backend: VectorPersistenceBackend | None = None,
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

        self._persistence = persistence_backend or MySQLVectorPersistenceBackend(
            host=mysql_host,
            port=mysql_port,
            user=mysql_user,
            password=mysql_password,
            database=mysql_database,
            collection_name=collection_name,
            vector_dimension=dimension,
            distance_metric=distance,
        )
        # Compatibility alias for existing diagnostics and tests.
        self._mysql = self._persistence
        self._last_sync_time = 0.0
        self._index_lock = threading.RLock()

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
        self._qdrant._ensure_collection()
        self._qdrant._collection_initialized = True

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
        self._persistence.save_batch(points)
        log_storage_event(
            logger,
            logging.DEBUG,
            "durable_vector_write_success",
            component="qdrant_mysql_vector_store",
            operation="upsert",
            validation_phase="operation",
            backend="mysql",
            target_resource=self.collection_name,
            durable_write_success=True,
        )

        # 2. Update local Qdrant index
        try:
            self._upsert_local(points)
        except Exception as e:
            log_storage_error(
                logger,
                "qdrant_write_failure_mysql_ok",
                component="qdrant_mysql_vector_store",
                operation="upsert",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                durable_write_success=True,
                qdrant_index_success=False,
                rebuild_required=True,
            )
            raise RuntimeError(
                "DEGRADED_REBUILD_REQUIRED: "
                "QDRANT_INDEX_UPDATE_FAILED_AFTER_DURABLE_WRITE. Rebuild required"
            ) from e

    def update_payload_by_worker(self, worker_id: str, payload_updates: dict) -> int:
        """Write through all exact-owner points, including locally unseen ones."""
        with self._index_lock:
            points = [
                VectorPoint(id=point.id, vector=point.vector,
                            payload={**point.payload, **payload_updates})
                for point in self._persistence.load_all()
                if point.payload.get("worker_id") == worker_id
            ]
            self.upsert(points)
            return len(points)

    def delete_by_worker(self, worker_id: str) -> int:
        """Delete owned vectors from durable storage and the local index."""
        with self._index_lock:
            ids = [point.id for point in self._persistence.load_all()
                   if point.payload.get("worker_id") == worker_id]
            self.delete(ids)
            return len(ids)

    def delete(self, ids: List[str]) -> None:
        """Delete vectors by business IDs."""
        if not ids:
            return

        # 1. Delete from MySQL
        self._persistence.delete_batch(ids)

        # 2. Delete from local Qdrant
        try:
            self._delete_local(ids)
        except Exception as e:
            log_storage_error(
                logger,
                "qdrant_delete_failure_mysql_ok",
                component="qdrant_mysql_vector_store",
                operation="delete",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
            )
            raise RuntimeError(
                "DEGRADED_REBUILD_REQUIRED: durable delete succeeded but local "
                "Qdrant cleanup failed"
            ) from e

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
        with self._index_lock:
            self._ensure_client()
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
        self._ensure_client()
        return self._qdrant.size()

    def count(self) -> int:
        return self.size()

    def __len__(self) -> int:
        return self.size()

    def get_vector_ids(self) -> List[str]:
        self._ensure_client()
        return self._qdrant.get_vector_ids()

    def get(self, id: str) -> Optional[VectorPoint]:
        """Get a single vector point from MySQL durable backend."""
        for point in self._persistence.load_all():
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
        """Text search remains unsupported, as in the original public backend."""
        logger.warning("[QdrantMySQLVectorStore] text_search not implemented")
        return []

    def batch_text_search(
        self,
        queries: List[str],
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[List[VectorSearchHit]]:
        """Return one empty result per query; do not add a keyword fallback."""
        logger.warning("[QdrantMySQLVectorStore] batch_text_search not implemented")
        return [[] for _ in queries]

    # ------------------------------------------------------------------
    # Rebuild from MySQL
    # ------------------------------------------------------------------

    def rebuild_from_backend(self, batch_size: int = 100) -> dict[str, Any]:
        """Rebuild the local Qdrant index from the durable backend."""
        logger.info("[QdrantMySQLVectorStore] Rebuilding indexes from durable backend...")
        start = time.time()
        supports_incremental_sync = isinstance(
            self._persistence,
            IncrementalVectorPersistenceBackend,
        )
        rebuild_checkpoint = (
            self._persistence.get_last_modified_time()
            if supports_incremental_sync
            else 0.0
        )
        log_storage_event(
            logger,
            logging.INFO,
            "qdrant_rebuild_start",
            component="qdrant_mysql_vector_store",
            operation="rebuild",
            validation_phase="operation",
            backend="qdrant+mysql",
            target_resource=self.collection_name,
        )

        with self._index_lock:
            self._ensure_client()
            all_points = self._persistence.load_all()
            durable_ids = {point.id for point in all_points}
            stale_ids = set(self._qdrant.get_vector_ids()) - durable_ids
            total_loaded = len(all_points)
            total_indexed = 0

            for i in range(0, len(all_points), batch_size):
                points = all_points[i:i + batch_size]
                self._qdrant.upsert(points)
                total_indexed += len(points)
            for point_id in stale_ids:
                self._qdrant.delete(point_id)
            if supports_incremental_sync:
                changes = self._persistence.load_changes_since(rebuild_checkpoint)
                if changes.upserts:
                    self._upsert_local(changes.upserts)
                if changes.deleted_ids:
                    self._delete_local(changes.deleted_ids)
                self._last_sync_time = max(
                    rebuild_checkpoint,
                    changes.checkpoint,
                )
            else:
                self._last_sync_time = self._persistence.get_last_modified_time()

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

    def rebuild_from_mysql(self, batch_size: int = 100) -> dict[str, Any]:
        """Rebuild indexes from MySQL (legacy public method name)."""
        return self.rebuild_from_backend(batch_size=batch_size)

    def sync_incremental(self) -> dict[str, int]:
        """Apply durable changes to the local indexes without writing them back."""
        if not isinstance(self._persistence, IncrementalVectorPersistenceBackend):
            raise RuntimeError(
                "configured persistence backend does not support incremental sync"
            )
        changes = self._persistence.load_changes_since(self._last_sync_time)
        with self._index_lock:
            if changes.upserts:
                self._upsert_local(changes.upserts)
            if changes.deleted_ids:
                self._delete_local(changes.deleted_ids)
            self._last_sync_time = max(self._last_sync_time, changes.checkpoint)
        return {
            "upserted": len(changes.upserts),
            "deleted": len(changes.deleted_ids),
        }

    def clear(self) -> None:
        """Clear both Qdrant and MySQL data. Use with caution."""
        try:
            self._ensure_client()
            self._qdrant.clear()
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to clear Qdrant: %s", e)
        try:
            ids = self._persistence.load_all()
            if ids:
                self._persistence.delete_batch([p.id for p in ids])
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to clear MySQL: %s", e)

    def close(self) -> None:
        self._qdrant.close()
        try:
            close = getattr(self._persistence, "close", None)
            if close is not None:
                close()
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to close MySQL: %s", e)

    def _upsert_local(self, points: list[VectorPoint]) -> None:
        with self._index_lock:
            self._ensure_client()
            self._qdrant.upsert(points)

    def _delete_local(self, ids: list[str]) -> None:
        with self._index_lock:
            self._ensure_client()
            for point_id in ids:
                self._qdrant.delete(point_id)


__all__ = ["QdrantMySQLVectorStore"]
