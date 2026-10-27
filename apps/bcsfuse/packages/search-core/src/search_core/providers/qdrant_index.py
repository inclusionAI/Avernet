"""Local dense and lexical retrieval over the same persisted point snapshot."""

from threading import RLock

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.keyword import QdrantKeywordIndex
from search_core.providers.qdrant import QdrantLocalVectorStore
from search_core.providers.qdrant_ids import _logical_id_from_result


class QdrantRetrievalIndex:
    """One application owns one local path; no Worker-ID lookup or model calls.

    Qdrant persists points. The sparse index is rebuilt from their payloads on
    startup. Mutations and searches share a lock because this is Local mode.
    """

    def __init__(self, *, path: str, dimension: int, collection_name: str = "search"):
        self._lock = RLock()
        self._dense = QdrantLocalVectorStore(
            path=path, dimension=dimension, collection_name=collection_name
        )
        self._keyword = QdrantKeywordIndex()
        try:
            self._dense._ensure_client()
            offset = None
            while True:
                records, offset = self._dense._client.scroll(
                    collection_name,
                    offset=offset,
                    limit=256,
                    with_vectors=False,
                )
                self._keyword.upsert(
                    [
                        VectorPoint(
                            id=_logical_id_from_result(
                                str(record.id), record.payload or {}
                            ),
                            vector=[],
                            payload=record.payload or {},
                        )
                        for record in records
                    ]
                )
                if offset is None:
                    break
        except Exception:
            self.close()
            raise

    def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        with self._lock:
            self._dense.upsert(points)
            self._keyword.upsert(points)

    def delete(self, ids: list[str]) -> None:
        with self._lock:
            for key in ids:
                self._dense.delete(key)
            self._keyword.delete(ids)

    @staticmethod
    def _filters(filters, allowed_ids, id_field):
        if any(key.startswith("_") for key in filters):
            raise ValueError("internal index fields cannot be caller filters")
        converted = {key: list(values) for key, values in filters.items()}
        if allowed_ids is not None:
            converted[id_field] = list(allowed_ids)
        return converted

    def vector_search(self, vector, limit, filters, allowed_ids):
        if allowed_ids == frozenset() or any(not values for values in filters.values()):
            return []
        with self._lock:
            return self._dense.search(
                vector,
                top_k=limit,
                filters=self._filters(filters, allowed_ids, "_external_id"),
            )

    def keyword_search(self, query, limit, filters, allowed_ids):
        if allowed_ids == frozenset() or any(not values for values in filters.values()):
            return []
        with self._lock:
            return self._keyword.search(
                query,
                limit,
                self._filters(filters, allowed_ids, "_keyword_external_id"),
            )

    def close(self):
        with self._lock:
            try:
                self._keyword.close()
            finally:
                self._dense.close()
