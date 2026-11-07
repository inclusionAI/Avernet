"""TC metadata helpers around shared Qdrant execution."""

import logging
import time
from app.infra.config.search_provider_settings import qdrant_path
from search_core.providers.qdrant import (
    QdrantLocalVectorStore as SharedQdrantLocalVectorStore,
)
from search_core.providers.qdrant import (
    QdrantClient,
    _QDRANT_CLIENT_CACHE,
    _QDRANT_CLIENT_CACHE_LOCK,
)
from search_core.providers.qdrant_ids import (
    _to_qdrant_point_id,
    _prepare_payload,
    _logical_id_from_result,
    _is_valid_qdrant_uuid,
    QDRANT_POINT_ID_NAMESPACE,
)
from search_core.providers.storage_logging import log_storage_event, log_storage_error
from qdrant_client.models import Filter, FieldCondition, MatchValue

logger = logging.getLogger(__name__)


class QdrantLocalVectorStore(SharedQdrantLocalVectorStore):
    def __init__(
        self,
        collection_name="bcsfuse_vectors",
        path=None,
        dimension=4096,
        distance="Cosine",
    ):
        super().__init__(
            collection_name=collection_name,
            path=qdrant_path(path),
            dimension=dimension,
            distance=distance,
        )

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
