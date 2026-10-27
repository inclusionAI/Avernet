"""Dense and lexical retrieval through Qdrant Local or Server."""

from search_core.config.qdrant import LOCAL_QDRANT, QdrantConnection
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.index_lock import IndexLock
from search_core.providers.keyword import QdrantKeywordIndex
from search_core.providers.qdrant_ids import _logical_id_from_result
from search_core.providers.qdrant_server import dense_store


class QdrantRetrievalIndex:
    """One owner per index; no Worker-ID lookup or model calls.

    Local rebuilds its sparse index from dense payloads at startup. Server keeps
    both collections persistent and allows concurrent reads; writes are exclusive.
    """

    def __init__(
        self,
        *,
        path: str,
        dimension: int,
        collection_name: str = "search",
        connection: QdrantConnection = LOCAL_QDRANT,
    ):
        self.connection = connection
        self._lock = IndexLock(concurrent_reads=connection.mode == "server")
        self._dense = dense_store(
            connection=connection,
            path=path,
            dimension=dimension,
            collection_name=collection_name,
        )
        self._keyword = QdrantKeywordIndex(
            connection=connection,
            collection_name=collection_name + "_keywords",
        )
        try:
            self._dense._ensure_client()
            self._keyword._ensure_client()
            if connection.mode == "server":
                return
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
        with self._lock.read():
            return self._dense.search(
                vector,
                top_k=limit,
                filters=self._filters(filters, allowed_ids, "_external_id"),
            )

    def keyword_search(self, query, limit, filters, allowed_ids):
        if allowed_ids == frozenset() or any(not values for values in filters.values()):
            return []
        with self._lock.read():
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
