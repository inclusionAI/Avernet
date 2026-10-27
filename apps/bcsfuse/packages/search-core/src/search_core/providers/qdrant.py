"""
Qdrant Local Vector Store

Local Qdrant vector store implementation for OSS deployments.

Supports multiple Qdrant client versions through feature detection.

S30A Status: Observability logging added for real storage validation.
S30C Status: Business ID to UUID mapping implemented.
"""

from typing import Optional, List, Dict, Any
import logging
import time
import builtins
import threading
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
)
from search_core.providers.storage_logging import (
    log_storage_event,
    log_storage_error,
    mask_url,
)


from search_core.providers.qdrant_ids import (
    _logical_id_from_result,
    _to_qdrant_point_id,
    _is_valid_qdrant_uuid,
)
from search_core.providers.qdrant_mutations import QdrantMutations

# Process-level cache for embedded Qdrant clients.
# qdrant-client local mode acquires a file lock per storage path and does not
# allow concurrent access even from the same process. Multiple
# QdrantLocalVectorStore instances targeting the same path therefore MUST
# share a single QdrantClient instance.
_QDRANT_CLIENT_CACHE: dict[str, tuple[Any, int]] = {}
_QDRANT_CLIENT_CACHE_LOCK = threading.Lock()


logger = logging.getLogger(__name__)


class QdrantLocalVectorStore(QdrantMutations):
    """
    Qdrant Local Vector Store for OSS.

    Uses Qdrant in local mode (no server required).
    Suitable for single-instance OSS deployments.

    Supports multiple Qdrant client versions.
    """

    def __init__(
        self,
        collection_name: str = "bcsfuse_vectors",
        path: str = "./qdrant_storage",
        dimension: int = 4096,
        distance: str = "Cosine",
    ):
        """Initialize Qdrant local vector store.

        Args:
            collection_name: Qdrant collection name.
            path: Storage path. If None, uses QDRANT_LOCAL_PATH env var or ./qdrant_storage.
            dimension: Vector dimension.
            distance: Distance metric (Cosine, Euclid, Dot).
        """
        self.collection_name = collection_name
        self.path = path
        self.dimension = dimension
        self.distance = Distance[distance.upper()]

        self._client = None
        self._collection_initialized = False

    def _ensure_client(self) -> None:
        """Ensure Qdrant client is initialized (lazy).

        Embedded Qdrant clients are cached per storage path so that multiple
        QdrantLocalVectorStore instances in the same process can safely share
        the same local storage. qdrant-client uses a file lock that blocks
        concurrent access, so the cache lookup and client creation must be
        performed atomically under the same lock.
        """
        if self._client is not None:
            return

        path = self.path
        component = "qdrant_local_vector_store"

        # Atomically check the cache and, if necessary, create and cache a new
        # client. Holding the lock for the whole initialization prevents two
        # threads from creating competing QdrantClient instances for the same
        # path (which would fail with AlreadyLocked on the underlying file lock).
        with _QDRANT_CLIENT_CACHE_LOCK:
            cached = _QDRANT_CLIENT_CACHE.get(path)
            if cached is not None:
                client, refcount = cached
                self._client = client
                try:
                    self._ensure_collection()
                except Exception:
                    self._client = None
                    raise
                self._collection_initialized = True
                _QDRANT_CLIENT_CACHE[path] = (client, refcount + 1)
                logger.debug(
                    "[QDRANT_CLIENT_REUSE] path=%s refcount=%d",
                    mask_url(path),
                    refcount + 1,
                )
                return

            start_time = time.time()

            log_storage_event(
                logger,
                logging.DEBUG,
                "qdrant_client_init_start",
                component=component,
                operation="init_client",
                validation_phase="setup",
                backend="qdrant",
                target_resource=self.collection_name,
                mode="local",
                url_or_path_masked=mask_url(path),
                dimension=self.dimension,
                distance=self.distance.name,
            )

            try:
                client = QdrantClient(path=path)
                duration_ms = (time.time() - start_time) * 1000

                log_storage_event(
                    logger,
                    logging.INFO,
                    "qdrant_client_init_success",
                    component=component,
                    operation="init_client",
                    validation_phase="setup",
                    backend="qdrant",
                    target_resource=self.collection_name,
                    duration_ms=duration_ms,
                )

                self._client = client
                if not self._collection_initialized:
                    self._ensure_collection()
                    self._collection_initialized = True

                _QDRANT_CLIENT_CACHE[path] = (client, 1)

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000

                log_storage_error(
                    logger,
                    "qdrant_client_init_failure",
                    component=component,
                    operation="init_client",
                    validation_phase="setup",
                    backend="qdrant",
                    target_resource=self.collection_name,
                    error=e,
                    duration_ms=duration_ms,
                )

                raise

    def _ensure_collection(self) -> None:
        """Ensure collection exists."""
        if self._client is None:
            return

        component = "qdrant_local_vector_store"
        start_time = time.time()

        # Log collection check start
        log_storage_event(
            logger,
            logging.DEBUG,
            "qdrant_collection_check_start",
            component=component,
            operation="check_collection",
            validation_phase="schema_init",
            backend="qdrant",
            target_resource=self.collection_name,
        )

        try:
            # Feature detection: check if collection_exists method is available (newer versions)
            if hasattr(self._client, "collection_exists"):
                # New API
                if not self._client.collection_exists(self.collection_name):
                    # Log collection create start
                    log_storage_event(
                        logger,
                        logging.DEBUG,
                        "qdrant_collection_create_start",
                        component=component,
                        operation="create_collection",
                        validation_phase="schema_init",
                        backend="qdrant",
                        target_resource=self.collection_name,
                        dimension=self.dimension,
                        distance=self.distance.name,
                    )

                    self._client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=VectorParams(
                            size=self.dimension,
                            distance=self.distance,
                        ),
                    )
            else:
                # Old API
                collections = self._client.get_collections().collections
                collection_names = [c.name for c in collections]

                if self.collection_name not in collection_names:
                    # Log collection create start
                    log_storage_event(
                        logger,
                        logging.DEBUG,
                        "qdrant_collection_create_start",
                        component=component,
                        operation="create_collection",
                        validation_phase="schema_init",
                        backend="qdrant",
                        target_resource=self.collection_name,
                        dimension=self.dimension,
                        distance=self.distance.name,
                    )

                    self._client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=VectorParams(
                            size=self.dimension,
                            distance=self.distance,
                        ),
                    )

            duration_ms = (time.time() - start_time) * 1000

            # Log collection ready
            log_storage_event(
                logger,
                logging.INFO,
                "qdrant_collection_ready",
                component=component,
                operation="ensure_collection",
                validation_phase="schema_init",
                backend="qdrant",
                target_resource=self.collection_name,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            # Log collection init failure
            log_storage_error(
                logger,
                "qdrant_collection_init_failure",
                component=component,
                operation="ensure_collection",
                validation_phase="schema_init",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                duration_ms=duration_ms,
            )

            raise

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors.

        Args:
            query_vector: Query vector.
            top_k: Number of results to return.
            filter: Optional filter dict (deprecated, use filters instead).
            filters: Optional filter dict (preferred).

        Returns:
            List of search results with id, score, and metadata.
        """
        self._ensure_client()

        # Support both 'filter' and 'filters' for backward compatibility
        effective_filter = filters if filters is not None else filter

        client_inst_id = builtins.id(self._client)
        logger.info(
            f"[Qdrant] Searching collection {self.collection_name}, dimension={len(query_vector)}, client_id={client_inst_id}, path={self.path}, filters={effective_filter}"
        )

        # Phase 2.3.5: FILTER-TRACE - Log filter input values and types
        if effective_filter:
            logger.info(
                "[FILTER-TRACE] stage=qdrant_search_input, filters=%s, filter_value_types=%s",
                effective_filter,
                {k: f"{v}({type(v).__name__})" for k, v in effective_filter.items()},
            )

        # Check dimension mismatch
        if len(query_vector) != self.dimension:
            log_storage_event(
                logger,
                logging.WARNING,
                "qdrant_vector_dimension_mismatch",
                component="qdrant_local_vector_store",
                operation="search",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                expected_dimension=self.dimension,
                actual_dimension=len(query_vector),
            )
            raise ValueError(
                f"Vector dimension mismatch: expected {self.dimension}, got {len(query_vector)}"
            )

        try:
            # Convert filter to Qdrant filter
            qdrant_filter = None
            if effective_filter:
                conditions = []
                for k, v in effective_filter.items():
                    # Handle multi-value filters (e.g., availability=['protected', 'public'])
                    if isinstance(v, list):
                        # Phase 2.6.1: Use MatchAny for list values (OR condition)
                        # MatchAny matches if the field value is ANY of the values in the list
                        conditions.append(FieldCondition(key=k, match=MatchAny(any=v)))
                        logger.debug(f"[Qdrant] Applied MatchAny filter: {k}={v}")
                    else:
                        # Single value filter
                        conditions.append(
                            FieldCondition(key=k, match=MatchValue(value=v))
                        )

                if conditions:
                    qdrant_filter = Filter(must=conditions)
                    logger.info(
                        f"[Qdrant] Applied filter: {len(conditions)} conditions"
                    )
                    # Phase 2.3.5: FILTER-TRACE - Log Qdrant filter structure
                    logger.info(
                        "[FILTER-TRACE] stage=qdrant_filter_built, filter=%s",
                        qdrant_filter,
                    )
                else:
                    logger.info(f"[Qdrant] No valid single-value filters to apply")

            # query_points is supported by the declared client dependency.
            # Backend failures must not turn into a successful empty result.
            results = self._client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=top_k,
                query_filter=qdrant_filter,
            ).points

            logger.info(
                f"[Qdrant] Search found {len(results)} raw results in collection {self.collection_name}"
            )

            # Import VectorSearchHit here to avoid circular dependency
            from search_core.plugin_api.vector_search_hit import VectorSearchHit

            # Map results back to logical IDs and convert to VectorSearchHit
            mapped_results = []
            for result in results:
                payload = result.payload or {}

                # Extract logical ID from payload
                logical_id = _logical_id_from_result(str(result.id), payload)

                # Remove internal _external_id from metadata if present
                if "_external_id" in payload:
                    payload = {k: v for k, v in payload.items() if k != "_external_id"}

                # Return VectorSearchHit object instead of dict
                mapped_results.append(
                    VectorSearchHit(
                        id=logical_id,
                        score=result.score,
                        payload=payload,
                    )
                )

            return mapped_results
        except Exception as e:
            log_storage_error(
                logger,
                "qdrant_search_failure",
                component="qdrant_local_vector_store",
                operation="search",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                top_k=top_k,
                filter_keys=list(filter.keys()) if filter else [],
            )
            raise

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        """Get vector by ID.

        Supports both UUID IDs and business IDs (e.g., worker_id:profile_id).
        Returns the logical ID (business ID or UUID) that was originally provided.

        Args:
            id: Vector ID (UUID or business ID).

        Returns:
            Vector data with metadata, or None if not found.
        """
        self._ensure_client()
        start_time = time.time()
        component = "qdrant_local_vector_store"

        # Map external ID to Qdrant point ID
        external_id = id
        point_id = _to_qdrant_point_id(external_id)
        is_uuid_input = _is_valid_qdrant_uuid(external_id)

        # Log ID mapping
        log_storage_event(
            logger,
            logging.DEBUG,
            "business_id_mapping_start",
            component=component,
            operation="get",
            validation_phase="id_mapping",
            backend="qdrant",
            target_resource=self.collection_name,
            point_id_type="uuid" if is_uuid_input else "business_id",
            external_id_present=True,
            mapping_strategy="direct" if is_uuid_input else "uuid5_deterministic",
            mapping_deterministic=True,
        )

        try:
            results = self._client.retrieve(
                collection_name=self.collection_name,
                ids=[point_id],
                with_vectors=True,  # Explicitly request vector data
            )

            if not results:
                return None

            result = results[0]
            payload = result.payload or {}

            # Extract logical ID from payload
            logical_id = _logical_id_from_result(str(result.id), payload)

            # Remove internal _external_id from metadata if present
            if "_external_id" in payload:
                payload = {k: v for k, v in payload.items() if k != "_external_id"}

            duration_ms = (time.time() - start_time) * 1000

            # Log mapping success
            log_storage_event(
                logger,
                logging.DEBUG,
                "business_id_mapping_success",
                component=component,
                operation="get",
                validation_phase="id_mapping",
                backend="qdrant",
                target_resource=self.collection_name,
                point_id_type="uuid" if is_uuid_input else "business_id",
                duration_ms=duration_ms,
            )

            return {
                "id": logical_id,
                "vector": result.vector,
                "metadata": payload,
            }
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "qdrant_get_failure",
                component=component,
                operation="get",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                point_id_type="uuid" if is_uuid_input else "business_id",
                duration_ms=duration_ms,
            )
            raise

    def get_vector_ids(self) -> list[str]:
        """Get all vector IDs in the collection.

        Uses Qdrant scroll API to iterate all points and extract business IDs
        from the ``_external_id`` payload field.  Aligned with the internal
        ``QdrantZdasVectorStore.get_vector_ids()`` implementation.

        Returns:
            List of external (business) IDs stored in the collection.
        """
        self._ensure_client()
        all_ids: list[str] = []
        try:
            scroll_result = self._client.scroll(
                collection_name=self.collection_name,
                limit=1000,
                with_payload=True,  # need payload to extract _external_id
                with_vectors=False,
            )

            while scroll_result:
                points, next_page_offset = scroll_result
                for point in points:
                    # Extract business ID from payload (mirrors _prepare_payload)
                    external_id = point.payload.get("_external_id", str(point.id))
                    all_ids.append(external_id)

                if not next_page_offset:
                    break

                scroll_result = self._client.scroll(
                    collection_name=self.collection_name,
                    offset=next_page_offset,
                    limit=1000,
                    with_payload=True,
                    with_vectors=False,
                )

        except Exception as e:
            logger.warning("[QDRANT_VECTORSTORE] get_vector_ids failed: %s", e)

        return all_ids

    def size(self) -> int:
        """Get collection size.

        Returns:
            Number of vectors in collection.
        """
        self._ensure_client()
        try:
            info = self._client.get_collection(self.collection_name)
            return info.points_count or 0
        except Exception:
            # Collection doesn't exist
            return 0

    def clear(self) -> None:
        """Clear all vectors in collection."""
        if self._client is not None and self._collection_initialized:
            try:
                self._client.delete_collection(self.collection_name)
            except Exception:
                # Collection might not exist, ignore
                pass
        self._collection_initialized = False

    def close(self) -> None:
        """Release the underlying Qdrant client if this instance holds the
        last cached reference for the storage path.

        This intentionally does not delete the collection, because the embedded
        Qdrant storage is meant to be durable across instances in the same
        process.
        """
        if self._client is None:
            return

        path = self.path
        with _QDRANT_CLIENT_CACHE_LOCK:
            cached = _QDRANT_CLIENT_CACHE.get(path)
            if cached is not None:
                client, refcount = cached
                refcount -= 1
                if refcount <= 0:
                    try:
                        client.close()
                    except Exception:
                        # Best-effort cleanup; OS/process lifecycle will finish
                        pass
                    _QDRANT_CLIENT_CACHE.pop(path, None)
                else:
                    _QDRANT_CLIENT_CACHE[path] = (client, refcount)
                    logger.debug(
                        "[QDRANT_CLIENT_CLOSE] path=%s remaining_refcount=%d",
                        mask_url(path),
                        refcount,
                    )

        self._client = None
        self._collection_initialized = False
