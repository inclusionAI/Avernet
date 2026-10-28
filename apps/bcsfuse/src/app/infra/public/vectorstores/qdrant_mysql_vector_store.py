"""
Qdrant + MySQL Vector Store (OSS production durable backend)

Aligns with the internal QdrantZdasVectorStore interface and the
VectorStoreAdapter protocol.

Architecture:
- MySQL (bcsfuse_vector_points): durable source of truth
- Qdrant Local or Server: rebuildable vector and keyword indexes
- Write-through: MySQL first, then Qdrant
- Rebuild: page durable vectors into Qdrant on startup/request
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from search_core.config.qdrant import LOCAL_QDRANT, QdrantConnection
from search_core.core.index_sync import apply_change_pages, apply_snapshot_pages
from search_core.providers.index_lock import IndexLock
from search_core.providers.qdrant_server import QdrantServerVectorStore

from app.domain.models.vector_point import VectorPoint
from app.domain.models.vector_search_hit import VectorSearchHit
from app.domain.services.vector_persistence_backend import (
    BatchedIncrementalVectorPersistenceBackend,
    BatchedVectorPersistenceBackend,
    IncrementalVectorPersistenceBackend,
    PointLookupVectorPersistenceBackend,
    VectorPersistenceBackend,
    WorkerScopedVectorPersistenceBackend,
)
from app.domain.services.vector_store_adapter import VectorStoreAdapter
from app.infra.public.observability.storage_logging import (
    log_storage_error,
    log_storage_event,
)
from app.infra.public.vectorstores.qdrant_keyword_index import QdrantKeywordIndex
from app.infra.public.vectorstores.qdrant_local_vector_store import (
    QdrantLocalVectorStore,
)
from app.infra.vectorstore_backends.mysql_vector_persistence_backend import (
    MySQLVectorPersistenceBackend,
)

logger = logging.getLogger(__name__)


class QdrantMySQLVectorStore(VectorStoreAdapter):
    """Write-through durable vector store with configurable Qdrant indexes.

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
        connection: QdrantConnection = LOCAL_QDRANT,
    ):
        self.collection_name = collection_name
        self.dimension = dimension
        self.distance = distance

        self.connection = connection
        if connection.mode == "server":
            self._qdrant = QdrantServerVectorStore(
                connection=connection, collection_name=collection_name,
                dimension=dimension, distance=distance,
            )
        else:
            self._qdrant = QdrantLocalVectorStore(
                collection_name=collection_name, path=qdrant_path,
                dimension=dimension, distance=distance,
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
        self._index_lock = IndexLock(concurrent_reads=connection.mode == "server")
        self._keywords = self._new_keyword_index()
        self._keywords_ready = True

        logger.info(
            "[QdrantMySQLVectorStore] Initialized collection=%s dimension=%d distance=%s",
            collection_name, dimension, distance,
        )

    def _new_keyword_index(self):
        return QdrantKeywordIndex(
            connection=self.connection,
            collection_name=self.collection_name + "_keywords",
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

    def upsert(self, points: List[VectorPoint], *, timing_worker_id: str | None = None) -> None:
        """Insert or update vector points.

        Write-through: durable MySQL first, then local Qdrant index.
        Qdrant index failures are reported after the durable write succeeds.
        """
        if not points:
            return

        # 1. Durable write to MySQL (must succeed)
        write_started_at = time.perf_counter()
        write_status = "failed"
        try:
            self._persistence.save_batch(points)
            write_status = "ok"
        finally:
            if timing_worker_id is not None:
                logger.info(
                    "[VECTOR-TIMING] phase=durable_write worker_id=%s backend=%s "
                    "points=%d status=%s elapsed_ms=%.1f",
                    timing_worker_id, type(self._persistence).__name__, len(points),
                    write_status, (time.perf_counter() - write_started_at) * 1000,
                )
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
        local_started_at = time.perf_counter()
        local_status = "failed"
        try:
            self._upsert_local(points, timing_worker_id=timing_worker_id)
            local_status = "ok"
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
        finally:
            if timing_worker_id is not None:
                logger.info(
                    "[VECTOR-TIMING] phase=local_index worker_id=%s points=%d status=%s elapsed_ms=%.1f",
                    timing_worker_id, len(points), local_status,
                    (time.perf_counter() - local_started_at) * 1000,
                )

    def update_payload_by_worker(self, worker_id: str, payload_updates: dict) -> int:
        """Write through all exact-owner points, including locally unseen ones."""
        started_at = time.perf_counter()
        lock_wait_ms = read_ms = prepare_ms = upsert_ms = 0.0
        read_method = "unknown"
        candidates = updated = 0
        outcome = "failed"
        try:
            with self._index_lock:
                lock_wait_ms = (time.perf_counter() - started_at) * 1000
                phase_started_at = time.perf_counter()
                if isinstance(self._persistence, WorkerScopedVectorPersistenceBackend):
                    read_method = "load_by_worker"
                    source_points = self._persistence.load_by_worker(worker_id)
                else:
                    read_method = "load_all"
                    source_points = self._persistence.load_all()
                read_ms = (time.perf_counter() - phase_started_at) * 1000
                candidates = len(source_points)
                phase_started_at = time.perf_counter()
                points = [
                    VectorPoint(id=point.id, vector=point.vector,
                                payload={**point.payload, **payload_updates})
                    for point in source_points
                    if point.payload.get("worker_id") == worker_id
                ]
                prepare_ms = (time.perf_counter() - phase_started_at) * 1000
                updated = len(points)
                phase_started_at = time.perf_counter()
                self.upsert(points, timing_worker_id=worker_id)
                upsert_ms = (time.perf_counter() - phase_started_at) * 1000
                outcome = "ok"
                return updated
        finally:
            logger.info(
                "[VECTOR-TIMING] phase=payload_sync worker_id=%s backend=%s read_method=%s "
                "candidates=%d updated=%d status=%s lock_wait_ms=%.1f read_ms=%.1f "
                "prepare_ms=%.1f upsert_ms=%.1f total_ms=%.1f",
                worker_id, type(self._persistence).__name__, read_method,
                candidates, updated, outcome, lock_wait_ms, read_ms,
                prepare_ms, upsert_ms, (time.perf_counter() - started_at) * 1000,
            )

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
        with self._index_lock.read():
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

    def check_ready(self) -> None:
        """Probe both Server collections; a running Python process is insufficient."""
        with self._index_lock.read():
            if not self._keywords_ready:
                raise RuntimeError("keyword index requires rebuild")
            if self.connection.mode == "server":
                self._ensure_client()
                self._keywords._ensure_client()
                self._qdrant._client.get_collection(self.collection_name)
                self._keywords._client.get_collection(self._keywords.collection_name)

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
        if isinstance(self._persistence, PointLookupVectorPersistenceBackend):
            return self._persistence.load_by_id(id)
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
        """Search the derived sparse index with filters applied before top-k."""
        with self._index_lock.read():
            if not self._keywords_ready:
                raise RuntimeError("keyword index requires rebuild")
            return self._keywords.search(query, top_k, filters)

    def batch_text_search(
        self,
        queries: List[str],
        top_k: int,
        filters: Optional[dict] = None,
    ) -> List[List[VectorSearchHit]]:
        """Use the same keyword and filter semantics for each query."""
        return [self.text_search(query, top_k, filters) for query in queries]

    # ------------------------------------------------------------------
    # Rebuild from MySQL
    # ------------------------------------------------------------------

    def rebuild_from_backend(self, batch_size: int = 100) -> dict[str, Any]:
        """Rebuild the local Qdrant index from the durable backend."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if not isinstance(self._persistence, BatchedVectorPersistenceBackend):
            raise TypeError("persistence backend must support bounded iter_batches")
        logger.info("[QdrantMySQLVectorStore] Rebuilding indexes from durable backend...")
        start = time.time()
        supports_incremental_sync = isinstance(
            self._persistence,
            IncrementalVectorPersistenceBackend,
        )
        if supports_incremental_sync and not isinstance(
            self._persistence, BatchedIncrementalVectorPersistenceBackend
        ):
            raise TypeError("incremental backend must support bounded change batches")
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
            keywords = self._new_keyword_index()
            old_keywords = self._keywords
            self._keywords_ready = False
            swapped_keywords = False
            total_loaded = 0
            total_indexed = 0
            try:
                stale_ids = set(self._qdrant.get_vector_ids())
                stale_ids.update(keywords.get_vector_ids())
                def restore(points):
                    keywords.upsert(points)
                    self._qdrant.upsert(points)

                total_loaded = apply_snapshot_pages(
                    self._persistence.iter_batches(batch_size), batch_size,
                    restore, stale_ids,
                )
                total_indexed = total_loaded
                for point_id in stale_ids:
                    self._qdrant.delete(point_id)

                keywords.delete(list(stale_ids))
                self._keywords = keywords
                swapped_keywords = True
                if supports_incremental_sync:
                    self._last_sync_time = rebuild_checkpoint
                    apply_change_pages(
                        self._persistence.iter_change_batches_since(rebuild_checkpoint, batch_size),
                        rebuild_checkpoint, self._upsert_local, self._delete_local,
                        lambda value: setattr(self, "_last_sync_time", value),
                    )
                else:
                    self._last_sync_time = self._persistence.get_last_modified_time()
                self._keywords_ready = True
            finally:
                if swapped_keywords:
                    old_keywords.close()
                else:
                    keywords.close()

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
        if not isinstance(self._persistence, BatchedIncrementalVectorPersistenceBackend):
            raise RuntimeError("configured persistence backend does not support bounded sync")
        with self._index_lock:
            return apply_change_pages(
                self._persistence.iter_change_batches_since(self._last_sync_time, 100),
                self._last_sync_time, self._upsert_local, self._delete_local,
                lambda value: setattr(self, "_last_sync_time", value),
            )

    def clear(self) -> None:
        """Delete durable records first, then rebuild both derived indexes."""
        with self._index_lock:
            ids = [p.id for p in self._persistence.load_all()]
            if ids:
                self._persistence.delete_batch(ids)
            self.rebuild_from_backend()

    def close(self) -> None:
        self._keywords.close()
        self._qdrant.close()
        try:
            close = getattr(self._persistence, "close", None)
            if close is not None:
                close()
        except Exception as e:
            logger.warning("[QdrantMySQLVectorStore] Failed to close MySQL: %s", e)

    def _upsert_local(
        self, points: list[VectorPoint], *, timing_worker_id: str | None = None,
    ) -> None:
        with self._index_lock:
            phase_started_at = time.perf_counter()
            self._ensure_client()
            if timing_worker_id is not None:
                logger.info(
                    "[VECTOR-TIMING] phase=local_client_ready worker_id=%s elapsed_ms=%.1f",
                    timing_worker_id, (time.perf_counter() - phase_started_at) * 1000,
                )
            try:
                phase_started_at = time.perf_counter()
                self._qdrant.upsert(points)
                if timing_worker_id is not None:
                    logger.info(
                        "[VECTOR-TIMING] phase=dense_index worker_id=%s points=%d elapsed_ms=%.1f",
                        timing_worker_id, len(points), (time.perf_counter() - phase_started_at) * 1000,
                    )
                phase_started_at = time.perf_counter()
                self._keywords.upsert(points)
                if timing_worker_id is not None:
                    logger.info(
                        "[VECTOR-TIMING] phase=keyword_index worker_id=%s points=%d elapsed_ms=%.1f",
                        timing_worker_id, len(points), (time.perf_counter() - phase_started_at) * 1000,
                    )
            except Exception:
                self._keywords_ready = False
                raise

    def _delete_local(self, ids: list[str]) -> None:
        with self._index_lock:
            self._ensure_client()
            try:
                self._keywords.delete(ids)
                for point_id in ids:
                    self._qdrant.delete(point_id)
            except Exception:
                self._keywords_ready = False
                raise


__all__ = ["QdrantMySQLVectorStore"]
