"""Synchronize existing vector payloads without changing profiles or scores."""

from src.domain.models.vector_point import VectorPoint


def sync_worker_vector_state(store, worker_id: str, runtime_state: str) -> int:
    """Update every exact-owner fragment; propagate persistence failures.

    Durable stores can provide update_payload_by_worker to enumerate their
    authoritative backend, including vectors not yet present in the local index.
    The fallback supports the existing point APIs used by local providers.
    """
    store = getattr(store, "vector_store", store)
    update_worker = getattr(store, "update_payload_by_worker", None)
    if callable(update_worker):
        return update_worker(worker_id, {"runtime_state": runtime_state})

    updates = []
    for vector_id in store.get_vector_ids():
        point = store.get(vector_id)
        if point is None:
            continue
        payload = point.get("metadata", point.get("payload", {})) if isinstance(point, dict) else point.payload
        if payload.get("worker_id") != worker_id:
            continue
        vector = point["vector"] if isinstance(point, dict) else point.vector
        updates.append(VectorPoint(
            id=vector_id, vector=vector, payload={**payload, "runtime_state": runtime_state},
        ))
    if updates:
        store.upsert(updates)
    return len(updates)
