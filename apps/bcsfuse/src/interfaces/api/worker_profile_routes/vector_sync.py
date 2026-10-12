"""Worker/Profile compatibility routes: vector_sync."""

import logging


logger = logging.getLogger(__name__)


def _sync_availability_to_vector_store(worker_id: str, availability: str) -> None:
    """Update availability field in vector payload so search filters work.

    When PUT /availability updates the DB, the vector payload still holds
    the old value. Search uses payload filters, so without this sync the
    filter would be stale.

    Supports both Qdrant (native set_payload) and Faiss (direct metadata
    update via update_payload_by_worker).

    Raises when the payload cannot be updated so lifecycle callers can retry
    instead of treating stale discovery metadata as synchronized.
    """
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import _get_vector_match_service
        service = _get_vector_match_service()
        if service is None or service._vector_store is None:
            raise RuntimeError(
                f"vector_store not available for availability update: worker={worker_id}"
            )

        vector_store = service._vector_store

        # ---- Faiss path: direct payload update ----
        if hasattr(vector_store, 'update_payload_by_worker'):
            count = vector_store.update_payload_by_worker(
                worker_id, {"availability": availability},
            )
            logger.info(
                "[AVAILABILITY-VECTORSYNC] Faiss: updated availability=%s for "
                "worker=%s, fragments_updated=%d", availability, worker_id, count,
            )
            return

        # ---- Qdrant path: native set_payload ----
        # Ensure client is initialized
        vector_store._ensure_client()
        client = vector_store._client
        collection = vector_store.collection_name

        # Scroll all point IDs for this worker, then batch set_payload
        from qdrant_client.models import Filter, FieldCondition, MatchText

        worker_filter = Filter(
            must=[
                FieldCondition(key="worker_id", match=MatchText(text=worker_id))
            ]
        )

        # Collect all point IDs for this worker
        point_ids = []
        offset = None
        while True:
            records, offset = client.scroll(
                collection,
                scroll_filter=worker_filter,
                limit=100,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.extend([r.id for r in records])
            if offset is None or not records:
                break

        if not point_ids:
            logger.info(
                "[AVAILABILITY-VECTORSYNC] No vectors found for worker=%s, skip", worker_id
            )
            return

        # Batch update availability in payload
        client.set_payload(
            collection_name=collection,
            payload={"availability": availability},
            points=point_ids,
            wait=True,
        )

        logger.info(
            "[AVAILABILITY-VECTORSYNC] Qdrant: updated availability=%s for worker=%s, "
            "fragments_updated=%d", availability, worker_id, len(point_ids)
        )
    except Exception as e:
        logger.error(
            "[AVAILABILITY-VECTORSYNC] Failed to sync availability to vector store "
            "for worker=%s: %s", worker_id, e
        )
        raise


def _sync_runtime_state_to_vector_store(worker_id: str, runtime_state: str) -> None:
    """Update runtime_state field in vector payload so search filters work.

    When PUT /online or /offline updates the DB, the vector payload still holds
    the old value. Search uses payload filters (e.g. {"runtime_state": ["online"]}),
    so without this sync the filter would be stale.

    Supports both Qdrant (native set_payload) and Faiss (direct metadata
    update via update_payload_by_worker).

    Non-critical: errors are logged but do not fail the HTTP request.
    """
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import _get_vector_match_service
        service = _get_vector_match_service()
        if service is None or service._vector_store is None:
            logger.warning(
                "[RUNTIMESTATE-VECTORSYNC] vector_store not available, "
                "skipping payload update for worker=%s", worker_id
            )
            return

        vector_store = service._vector_store

        # ---- Faiss path: direct payload update ----
        if hasattr(vector_store, 'update_payload_by_worker'):
            count = vector_store.update_payload_by_worker(
                worker_id, {"runtime_state": runtime_state},
            )
            logger.info(
                "[RUNTIMESTATE-VECTORSYNC] Faiss: updated runtime_state=%s for "
                "worker=%s, fragments_updated=%d", runtime_state, worker_id, count,
            )
            return

        # ---- Qdrant path: native set_payload ----
        vector_store._ensure_client()
        client = vector_store._client
        collection = vector_store.collection_name

        from qdrant_client.models import Filter, FieldCondition, MatchText

        worker_filter = Filter(
            must=[
                FieldCondition(key="worker_id", match=MatchText(text=worker_id))
            ]
        )

        point_ids = []
        offset = None
        while True:
            records, offset = client.scroll(
                collection,
                scroll_filter=worker_filter,
                limit=100,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.extend([r.id for r in records])
            if offset is None or not records:
                break

        if not point_ids:
            logger.info(
                "[RUNTIMESTATE-VECTORSYNC] No vectors found for worker=%s, skip",
                worker_id,
            )
            return

        client.set_payload(
            collection_name=collection,
            payload={"runtime_state": runtime_state},
            points=point_ids,
            wait=True,
        )

        logger.info(
            "[RUNTIMESTATE-VECTORSYNC] Qdrant: updated runtime_state=%s for "
            "worker=%s, fragments_updated=%d",
            runtime_state, worker_id, len(point_ids),
        )
    except Exception as e:
        logger.warning(
            "[RUNTIMESTATE-VECTORSYNC] Failed to sync runtime_state to vector "
            "store for worker=%s: %s", worker_id, e,
        )
