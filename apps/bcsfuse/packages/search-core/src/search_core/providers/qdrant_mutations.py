"""
Qdrant Local Vector Store

Local Qdrant vector store implementation for OSS deployments.

Supports multiple Qdrant client versions through feature detection.

S30A Status: Observability logging added for real storage validation.
S30C Status: Business ID to UUID mapping implemented.
"""

from typing import Optional, List, Dict, Any, Union
import logging
import time
import builtins
from qdrant_client.models import (
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from search_core.providers.storage_logging import (
    log_storage_event,
    log_storage_error,
)


from search_core.providers.qdrant_ids import (
    _to_qdrant_point_id,
    _prepare_payload,
    _is_valid_qdrant_uuid,
)

logger = logging.getLogger("search_core.providers.qdrant")


class QdrantMutations:
    def upsert(self, *args, **kwargs) -> Union[bool, None]:
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
        from search_core.plugin_api.vector_point import VectorPoint

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
            metadata = args[2] if len(args) >= 3 else kwargs.get("metadata", None)
            return self._upsert_one(id, vector, metadata)

        # Invalid call pattern
        raise TypeError(
            f"upsert() supports either:\n"
            f"  1. upsert(id: str, vector: List[float], metadata: Optional[Dict]) -> bool\n"
            f"  2. upsert(points: list[VectorPoint]) -> None\n"
            f"Got: args={len(args)}, kwargs={list(kwargs.keys())}"
        )

    def _upsert_one(
        self, id: str, vector: List[float], metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
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
        logger.info(
            f"[QDRANT_VECTORSTORE_UPSERT] mode=single, collection={self.collection_name}, vector_id={id}, dimension={len(vector)}, client_id={client_inst_id}, path={self.path}"
        )

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
            raise ValueError(
                f"Vector dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )

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

            logger.info(
                f"[QDRANT_VECTORSTORE_UPSERT] mode=single, result=success, points_count=1, collection={self.collection_name}, duration_ms={duration_ms:.2f}"
            )

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

            logger.error(
                f"[QDRANT_VECTORSTORE_UPSERT] mode=single, result=fail, collection={self.collection_name}, error={e}, duration_ms={duration_ms:.2f}"
            )
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

        self._ensure_client()
        start_time = time.time()
        component = "qdrant_local_vector_store"

        client_inst_id = builtins.id(self._client)
        points_count = len(points)

        # Extract vector dimension from first point
        if points_count == 0:
            logger.info(
                f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, result=skip, points_count=0, collection={self.collection_name}"
            )
            return

        first_point = points[0]
        vector_dimension = len(first_point.vector)

        logger.info(
            f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, collection={self.collection_name}, points_count={points_count}, dimension={vector_dimension}, client_id={client_inst_id}, path={self.path}"
        )

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
            raise ValueError(
                f"Vector dimension mismatch: expected {self.dimension}, got {vector_dimension}"
            )

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

            logger.error(
                f"[QDRANT_VECTORSTORE_UPSERT] mode=batch, result=fail, points_count={points_count}, collection={self.collection_name}, error={e}, duration_ms={duration_ms:.2f}"
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
            logger.info(
                f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, result=skip, updates_count=0, collection={self.collection_name}"
            )
            return 0

        updates_count = len(updates)
        logger.info(
            f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, collection={self.collection_name}, updates_count={updates_count}"
        )

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

            logger.error(
                f"[QDRANT_VECTORSTORE_UPDATE_PAYLOADS] mode=batch, result=fail, updates_count={updates_count}, collection={self.collection_name}, error={e}, duration_ms={duration_ms:.2f}"
            )
            raise
