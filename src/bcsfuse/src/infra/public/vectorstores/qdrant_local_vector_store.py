"""
Qdrant Local Vector Store

Local Qdrant vector store implementation for OSS deployments.

Supports multiple Qdrant client versions through feature detection.

S30A Status: Observability logging added for real storage validation.
S30C Status: Business ID to UUID mapping implemented.
"""
from typing import Optional, List, Dict, Any, Union, overload
import os
import logging
import time
import uuid
import builtins
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
)
from src.infra.public.observability.storage_logging import (
    log_storage_event,
    log_storage_error,
    mask_url,
)

logger = logging.getLogger(__name__)

# Stable UUID namespace for deterministic point ID mapping
# This namespace ensures consistent mapping across all instances
# IMPORTANT: This is a FIXED project namespace constant - must NEVER be regenerated
# Same value as uuid.NAMESPACE_DNS, but explicitly documented as project constant
# Deterministic mapping guarantees same business ID always maps to same UUID
QDRANT_POINT_ID_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')


def _is_valid_qdrant_uuid(value: str) -> bool:
    """Check if a string is a valid UUID format for Qdrant point IDs.

    Qdrant local mode requires strict UUID format for point IDs.
    This function validates whether a given string can be used directly
    as a Qdrant point ID.

    Args:
        value: String to validate.

    Returns:
        True if the string is a valid UUID format.
    """
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _to_qdrant_point_id(external_id: str) -> str:
    """Map external ID to Qdrant point ID.

    If the external ID is already a valid UUID, it is returned as-is.
    Otherwise, a deterministic UUID v5 is generated from the external ID
    using a stable namespace.

    This ensures:
    1. Same external ID always maps to same Qdrant point ID
    2. Mapping is stable across process restarts
    3. Mapping does not depend on timestamp, random seed, or local path
    4. UUID IDs are preserved for backward compatibility

    Args:
        external_id: External ID (can be UUID or business ID).

    Returns:
        Qdrant point ID (always a valid UUID string).
    """
    if _is_valid_qdrant_uuid(external_id):
        # Already a valid UUID, use directly
        return external_id

    # Generate deterministic UUID v5 from external ID
    point_id = str(uuid.uuid5(QDRANT_POINT_ID_NAMESPACE, external_id))

    return point_id


def _prepare_payload(external_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Prepare payload with external ID mapping.

    Ensures the external ID is stored in the payload so it can be
    retrieved later for returning logical IDs to callers.

    Args:
        external_id: External ID (business ID or UUID).
        payload: Original payload metadata.

    Returns:
        Payload with external_id field added.
    """
    result = dict(payload) if payload else {}
    result['_external_id'] = external_id
    return result


def _logical_id_from_result(point_id: str, payload: Dict[str, Any]) -> str:
    """Extract logical ID from Qdrant result.

    If the payload contains an external_id field, that is returned
    as the logical ID. Otherwise, the point ID is returned.

    This ensures:
    1. Business IDs are returned for vectors stored with business IDs
    2. UUID IDs are returned for vectors stored with UUID IDs
    3. Backward compatibility is maintained

    Args:
        point_id: Qdrant point ID (internal UUID).
        payload: Payload from Qdrant result.

    Returns:
        Logical ID (external_id or point_id).
    """
    if payload and '_external_id' in payload:
        return payload['_external_id']
    return point_id


class QdrantLocalVectorStore:
    """
    Qdrant Local Vector Store for OSS.

    Uses Qdrant in local mode (no server required).
    Suitable for single-instance OSS deployments.

    Supports multiple Qdrant client versions.
    """

    def __init__(
        self,
        collection_name: str = "bcsfuse_vectors",
        path: Optional[str] = None,
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
        self.path = path or os.getenv("QDRANT_LOCAL_PATH", "./qdrant_storage")
        self.dimension = dimension
        self.distance = Distance[distance.upper()]

        self._client = None
        self._collection_initialized = False
        # Lazy initialization - don't connect until first method call

    def _ensure_client(self) -> None:
        """Ensure Qdrant client is initialized (lazy)."""
        if self._client is None:
            start_time = time.time()
            component = "qdrant_local_vector_store"

            # Log client init start
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
                url_or_path_masked=mask_url(self.path),
                dimension=self.dimension,
                distance=self.distance.name,
            )

            try:
                self._client = QdrantClient(path=self.path)
                duration_ms = (time.time() - start_time) * 1000

                # Log client init success
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

                if not self._collection_initialized:
                    self._ensure_collection()
                    self._collection_initialized = True

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000

                # Log client init failure
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
            if hasattr(self._client, 'collection_exists'):
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

    def upsert(
        self,
        *args,
        **kwargs
    ) -> Union[bool, None]:
        """Insert or update vector(s).

        Compatible upsert supporting two call patterns:

        1. Single-point (legacy):
           upsert(id: str, vector: List[float], metadata: Optional[Dict] = None) -> bool

        2. Batch (VectorStoreAdapter protocol):
           upsert(points: list[VectorPoint]) -> None

        Args:
            For single-point:
                id: Vector ID (UUID or business ID).
                vector: Vector data.
                metadata: Optional metadata.
            For batch:
                points: List of VectorPoint objects.

        Returns:
            True if successful (single-point), None (batch).

        Raises:
            ValueError: If vector dimension mismatch.
            TypeError: If arguments don't match either pattern.
        """
        # Import VectorPoint here to avoid circular dependency
        from src.domain.models.vector_point import VectorPoint

        # Pattern 1: Batch upsert (VectorStoreAdapter protocol)
        # upsert(points: list[VectorPoint])
        if len(args) == 1 and isinstance(args[0], list):
            points = args[0]
            if points and isinstance(points[0], VectorPoint):
                return self._upsert_batch(points)

        # Pattern 2: Single-point upsert (legacy)
        # upsert(id: str, vector: List[float], metadata: Optional[Dict])
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], list):
            id = args[0]
            vector = args[1]
            metadata = args[2] if len(args) >= 3 else kwargs.get('metadata', None)
            return self._upsert_one(id, vector, metadata)

        # Invalid call pattern
        raise TypeError(
            f"upsert() supports either:\n"
            f"  1. upsert(id: str, vector: List[float], metadata: Optional[Dict]) -> bool\n"
            f"  2. upsert(points: list[VectorPoint]) -> None\n"
            f"Got: args={len(args)}, kwargs={list(kwargs.keys())}"
        )

    def _upsert_one(self, id: str, vector: List[float], metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Insert or update a single vector (internal helper).

        Supports both UUID IDs and business IDs (e.g., worker_id:profile_id).
        Business IDs are automatically mapped to deterministic UUIDs for Qdrant.

        Args:
            id: Vector ID (UUID or business ID).
            vector: Vector data.
            metadata: Optional metadata.

        Returns:
            True if successful.
        """
        self._ensure_client()
        start_time = time.time()
        component = "qdrant_local_vector_store"

        client_inst_id = builtins.id(self._client)
        logger.info(f"[QDRANT_VECTORSTORE_UPSERT] mode=single, collection={self.collection_name}, vector_id={id}, dimension={len(vector)}, client_id={client_inst_id}, path={self.path}")

        # Check dimension mismatch
        if len(vector) != self.dimension:
            log_storage_event(
                logger,
                logging.WARNING,
                "qdrant_vector_dimension_mismatch",
                component=component,
                operation="upsert",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                expected_dimension=self.dimension,
                actual_dimension=len(vector),
            )
            raise ValueError(f"Vector dimension mismatch: expected {self.dimension}, got {len(vector)}")

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
            operation="upsert",
            validation_phase="id_mapping",
            backend="qdrant",
            target_resource=self.collection_name,
            point_id_type="uuid" if is_uuid_input else "business_id",
            external_id_present=True,
            mapping_strategy="direct" if is_uuid_input else "uuid5_deterministic",
            mapping_deterministic=True,
        )

        # Prepare payload with external ID
        payload = _prepare_payload(external_id, metadata)

        try:
            point = PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )

            self._client.upsert(
                collection_name=self.collection_name,
                points=[point],
            )

            duration_ms = (time.time() - start_time) * 1000

            # Log mapping success
            log_storage_event(
                logger,
                logging.DEBUG,
                "business_id_mapping_success",
                component=component,
                operation="upsert",
                validation_phase="id_mapping",
                backend="qdrant",
                target_resource=self.collection_name,
                point_id_type="uuid" if is_uuid_input else "business_id",
                duration_ms=duration_ms,
            )

            logger.info(f"[QDRANT_VECTORSTORE_UPSERT] mode=single, result=success, points_count=1, collection={self.collection_name}, duration_ms={duration_ms:.2f}")

            return True
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "qdrant_upsert_failure",
                component=component,
                operation="upsert",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                vector_count=1,
                point_id_type="uuid" if is_uuid_input else "business_id",
                duration_ms=duration_ms,
            )

            logger.error(f"[QDRANT_VECTORSTORE_UPSERT] mode=single, result=fail, collection={self.collection_name}, error={e}, duration_ms={duration_ms:.2f}")
            raise

    def _upsert_batch(self, points: list) -> None:
        """Insert or update multiple vectors (internal helper).

        Implements VectorStoreAdapter protocol batch upsert.
        Maps business IDs to UUIDs transparently.

        Args:
            points: List of VectorPoint objects.

        Raises:
            ValueError: If vector dimension mismatch.
        """
        from src.domain.models.vector_point import VectorPoint

        self._ensure_client()
        start_time = time.time()
        component = "qdrant_local_vector_store"

        client_inst_id = builtins.id(self._client)
        points_count = len(points)

        # Extract vector dimension from first point
        if points_count == 0:
            logger.info(f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, result=skip, points_count=0, collection={self.collection_name}")
            return

        first_point = points[0]
        vector_dimension = len(first_point.vector)

        logger.info(f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, collection={self.collection_name}, points_count={points_count}, dimension={vector_dimension}, client_id={client_inst_id}, path={self.path}")

        # Check dimension mismatch
        if vector_dimension != self.dimension:
            log_storage_event(
                logger,
                logging.WARNING,
                "qdrant_vector_dimension_mismatch",
                component=component,
                operation="upsert_batch",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                expected_dimension=self.dimension,
                actual_dimension=vector_dimension,
            )
            raise ValueError(f"Vector dimension mismatch: expected {self.dimension}, got {vector_dimension}")

        try:
            # Convert VectorPoint list to Qdrant PointStruct list
            qdrant_points = []
            payload_keys_set = set()

            for point in points:
                # Map external ID to Qdrant point ID
                external_id = point.id
                point_id = _to_qdrant_point_id(external_id)

                # Prepare payload (point.payload is the field name in VectorPoint)
                payload = _prepare_payload(external_id, point.payload)
                payload_keys_set.update(payload.keys())

                qdrant_point = PointStruct(
                    id=point_id,
                    vector=point.vector,
                    payload=payload,
                )
                qdrant_points.append(qdrant_point)

            # Batch upsert to Qdrant
            self._client.upsert(
                collection_name=self.collection_name,
                points=qdrant_points,
            )

            duration_ms = (time.time() - start_time) * 1000
            payload_keys = sorted(list(payload_keys_set))

            logger.info(
                f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, result=success, "
                f"points_count={points_count}, collection={self.collection_name}, "
                f"dimension={vector_dimension}, payload_keys={payload_keys}, "
                f"duration_ms={duration_ms:.2f}"
            )

            # Log success
            log_storage_event(
                logger,
                logging.DEBUG,
                "qdrant_batch_upsert_success",
                component=component,
                operation="upsert_batch",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                points_count=points_count,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "qdrant_batch_upsert_failure",
                component=component,
                operation="upsert_batch",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                vector_count=points_count,
                duration_ms=duration_ms,
            )

            logger.error(f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, result=fail, points_count={points_count}, collection={self.collection_name}, error={e}, duration_ms={duration_ms:.2f}")
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
        logger.info(f"[Qdrant] Searching collection {self.collection_name}, dimension={len(query_vector)}, client_id={client_inst_id}, path={self.path}, filters={effective_filter}")

        # Phase 2.3.5: FILTER-TRACE - Log filter input values and types
        if effective_filter:
            logger.info(
                "[FILTER-TRACE] stage=qdrant_search_input, filters=%s, filter_value_types=%s",
                effective_filter,
                {k: f"{v}({type(v).__name__})" for k, v in effective_filter.items()}
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
            raise ValueError(f"Vector dimension mismatch: expected {self.dimension}, got {len(query_vector)}")

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
                        conditions.append(
                            FieldCondition(key=k, match=MatchAny(any=v))
                        )
                        logger.debug(f"[Qdrant] Applied MatchAny filter: {k}={v}")
                    else:
                        # Single value filter
                        conditions.append(
                            FieldCondition(key=k, match=MatchValue(value=v))
                        )

                if conditions:
                    qdrant_filter = Filter(must=conditions)
                    logger.info(f"[Qdrant] Applied filter: {len(conditions)} conditions")
                    # Phase 2.3.5: FILTER-TRACE - Log Qdrant filter structure
                    logger.info(
                        "[FILTER-TRACE] stage=qdrant_filter_built, filter=%s",
                        qdrant_filter
                    )
                else:
                    logger.info(f"[Qdrant] No valid single-value filters to apply")

            # Feature detection: try different search methods based on client version
            results = None

            # Try new API first (query_points)
            if hasattr(self._client, 'query_points'):
                try:
                    from qdrant_client.models import QueryRequest
                    query_result = self._client.query_points(
                        collection_name=self.collection_name,
                        query=query_vector,
                        limit=top_k,
                        query_filter=qdrant_filter,
                    )
                    results = query_result.points if hasattr(query_result, 'points') else query_result
                except Exception as e:
                    logger.debug(f"query_points failed, trying search: {e}")
                    results = None

            # Fall back to old API (search)
            if results is None and hasattr(self._client, 'search'):
                results = self._client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=top_k,
                    query_filter=qdrant_filter,
                )

            # If still no results, try query method
            if results is None and hasattr(self._client, 'query'):
                try:
                    query_result = self._client.query(
                        collection_name=self.collection_name,
                        query_vector=query_vector,
                        limit=top_k,
                        query_filter=qdrant_filter,
                    )
                    results = query_result if isinstance(query_result, list) else []
                except Exception as e:
                    logger.error(f"All Qdrant search methods failed: {e}")
                    results = []

            # Ensure we have a list
            if results is None:
                logger.warning(f"[Qdrant] Search returned None for collection {self.collection_name}")
                results = []

            logger.info(f"[Qdrant] Search found {len(results)} raw results in collection {self.collection_name}")

            # Import VectorSearchHit here to avoid circular dependency
            from src.domain.models.vector_search_hit import VectorSearchHit

            # Map results back to logical IDs and convert to VectorSearchHit
            mapped_results = []
            for result in results:
                payload = result.payload or {}

                # Extract logical ID from payload
                logical_id = _logical_id_from_result(str(result.id), payload)

                # Remove internal _external_id from metadata if present
                if '_external_id' in payload:
                    payload = {k: v for k, v in payload.items() if k != '_external_id'}

                # Return VectorSearchHit object instead of dict
                mapped_results.append(VectorSearchHit(
                    id=logical_id,
                    score=result.score,
                    payload=payload,
                ))

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
            if '_external_id' in payload:
                payload = {k: v for k, v in payload.items() if k != '_external_id'}

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

    def delete(self, id: str) -> bool:
        """Delete vector by ID.

        Supports both UUID IDs and business IDs (e.g., worker_id:profile_id).

        Args:
            id: Vector ID (UUID or business ID).

        Returns:
            True if deleted.
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
            operation="delete",
            validation_phase="id_mapping",
            backend="qdrant",
            target_resource=self.collection_name,
            point_id_type="uuid" if is_uuid_input else "business_id",
            external_id_present=True,
            mapping_strategy="direct" if is_uuid_input else "uuid5_deterministic",
            mapping_deterministic=True,
        )

        try:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=[point_id],
            )

            duration_ms = (time.time() - start_time) * 1000

            # Log mapping success
            log_storage_event(
                logger,
                logging.DEBUG,
                "business_id_mapping_success",
                component=component,
                operation="delete",
                validation_phase="id_mapping",
                backend="qdrant",
                target_resource=self.collection_name,
                point_id_type="uuid" if is_uuid_input else "business_id",
                duration_ms=duration_ms,
            )

            return True
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "qdrant_delete_failure",
                component=component,
                operation="delete",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                delete_mode="by_id",
                point_id_type="uuid" if is_uuid_input else "business_id",
                duration_ms=duration_ms,
            )
            raise

    def delete_by_filter(self, filter: Dict[str, Any]) -> int:
        """Delete vectors by filter.

        Args:
            filter: Filter criteria.

        Returns:
            Number of vectors deleted (Qdrant doesn't return count).
        """
        self._ensure_client()
        try:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filter.items()
            ]
            qdrant_filter = Filter(must=conditions)

            self._client.delete(
                collection_name=self.collection_name,
                points_selector=qdrant_filter,
            )

            # Qdrant doesn't return count, so we return 0
            return 0
        except Exception as e:
            log_storage_error(
                logger,
                "qdrant_delete_failure",
                component="qdrant_local_vector_store",
                operation="delete_by_filter",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                delete_mode="by_filter",
                filter_keys=list(filter.keys()) if filter else [],
            )
            raise

    def delete_by_worker(self, worker_id: str) -> int:
        """Delete all vectors for a worker.

        Args:
            worker_id: Worker ID.

        Returns:
            Number of vectors deleted (Qdrant doesn't return count).
        """
        return self.delete_by_filter({"worker_id": worker_id})

    def delete_by_profile(self, worker_id: str, profile_id: str) -> int:
        """Delete vector for a specific profile.

        Uses deterministic UUID mapping from business ID pattern:
        business_id = f"{worker_id}:{profile_id}"

        Args:
            worker_id: Worker ID.
            profile_id: Profile ID.

        Returns:
            Number of vectors deleted.
        """
        start_time = time.time()
        component = "qdrant_local_vector_store"

        # Construct business ID from worker_id and profile_id
        external_id = f"{worker_id}:{profile_id}"

        # Map to Qdrant point ID using deterministic UUID v5
        point_id = _to_qdrant_point_id(external_id)

        # Log ID mapping
        log_storage_event(
            logger,
            logging.DEBUG,
            "business_id_mapping_start",
            component=component,
            operation="delete_by_profile",
            validation_phase="id_mapping",
            backend="qdrant",
            target_resource=self.collection_name,
            point_id_type="business_id",
            external_id_present=True,
            mapping_strategy="uuid5_deterministic",
            mapping_deterministic=True,
            payload_filter_keys=["worker_id", "profile_id"],
        )

        try:
            # Delete by mapped UUID point ID
            self.delete(external_id)

            duration_ms = (time.time() - start_time) * 1000

            # Log mapping success
            log_storage_event(
                logger,
                logging.DEBUG,
                "business_id_mapping_success",
                component=component,
                operation="delete_by_profile",
                validation_phase="id_mapping",
                backend="qdrant",
                target_resource=self.collection_name,
                point_id_type="business_id",
                duration_ms=duration_ms,
            )

            return 1
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "qdrant_delete_by_profile_failure",
                component=component,
                operation="delete_by_profile",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                delete_mode="by_profile_business_id",
                point_id_type="business_id",
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
                with_payload=True,   # need payload to extract _external_id
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
            logger.warning(
                "[QDRANT_VECTORSTORE] get_vector_ids failed: %s", e
            )

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
        # Force re-initialization on next operation

    def update_payloads(self, updates: list[tuple[str, dict]]) -> int:
        """Update payloads for multiple vectors.

        Uses Qdrant's native set_payload API for efficient payload updates.
        Preserves existing vector data, only updates payload fields.

        Args:
            updates: List of (vector_id, new_payload) tuples.

        Returns:
            Number of vectors updated.

        Raises:
            Exception: If update fails.
        """
        self._ensure_client()
        start_time = time.time()
        component = "qdrant_local_vector_store"

        if not updates:
            logger.info(f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, result=skip, updates_count=0, collection={self.collection_name}")
            return 0

        updates_count = len(updates)
        logger.info(f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, collection={self.collection_name}, updates_count={updates_count}")

        updated_count = 0

        try:
            for vector_id, new_payload in updates:
                # Map external ID to Qdrant point ID
                external_id = vector_id
                point_id = _to_qdrant_point_id(external_id)

                # Prepare payload with external ID (preserve _external_id field)
                payload = _prepare_payload(external_id, new_payload)

                # Use Qdrant's set_payload API to update payload
                # This preserves the vector and only updates the payload
                self._client.set_payload(
                    collection_name=self.collection_name,
                    payload=payload,
                    points=[point_id],
                )

                updated_count += 1

            duration_ms = (time.time() - start_time) * 1000

            logger.info(
                f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, result=success, "
                f"updates_count={updated_count}, collection={self.collection_name}, "
                f"duration_ms={duration_ms:.2f}"
            )

            # Log success
            log_storage_event(
                logger,
                logging.DEBUG,
                "qdrant_update_payloads_success",
                component=component,
                operation="update_payloads",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                updates_count=updated_count,
                duration_ms=duration_ms,
            )

            return updated_count

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "qdrant_update_payloads_failure",
                component=component,
                operation="update_payloads",
                validation_phase="operation",
                backend="qdrant",
                target_resource=self.collection_name,
                error=e,
                updates_count=updates_count,
                duration_ms=duration_ms,
            )

            logger.error(f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, result=fail, updates_count={updates_count}, collection={self.collection_name}, error={e}, duration_ms={duration_ms:.2f}")
            raise