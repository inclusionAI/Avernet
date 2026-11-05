"""Dense and lexical retrieval through Qdrant Local or Server."""

import math

from qdrant_client import models

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
    keyword_index_fields optionally provisions keyword payload indexes on both
    Server collections at startup. Local ignores this performance-only option.
    Existing incompatible field indexes or provisioning failures fail startup.
    """

    def __init__(
        self,
        *,
        path: str,
        dimension: int,
        collection_name: str = "search",
        connection: QdrantConnection = LOCAL_QDRANT,
        read_timeout_seconds: float = 0.0,
        keyword_index_fields: frozenset[str] = frozenset(),
    ):
        if not math.isfinite(read_timeout_seconds) or read_timeout_seconds < 0:
            raise ValueError("read timeout must be finite and nonnegative")
        self.read_timeout_seconds = read_timeout_seconds
        self._query_options = (
            {"timeout": max(1, math.ceil(read_timeout_seconds))}
            if read_timeout_seconds and connection.mode == "server"
            else {}
        )
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
                self._ensure_keyword_indexes(keyword_index_fields)
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

    def _ensure_keyword_indexes(self, fields):
        # Startup only. Local supports filtering but has no payload indexes.
        if not fields:
            return
        for store in (self._dense, self._keyword):
            schema = store._client.get_collection(store.collection_name).payload_schema
            for field in sorted(fields):
                existing = schema.get(field)
                if existing is not None:
                    if existing.data_type != models.PayloadSchemaType.KEYWORD:
                        raise ValueError(f"filter index must be keyword: {field}")
                    continue
                store._client.create_payload_index(
                    collection_name=store.collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )

    def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        # Qdrant interprets a dotted filter key as a nested payload path.
        indexed = []
        for point in points:
            labels = {
                k[7:]: v for k, v in point.payload.items() if k.startswith("labels.")
            }
            if labels:
                payload = {
                    k: v
                    for k, v in point.payload.items()
                    if not k.startswith("labels.")
                }
                point = point.model_copy(
                    update={"payload": {**payload, "labels": labels}}
                )
            indexed.append(point)
        with self._lock:
            self._dense.upsert(indexed)
            self._keyword.upsert(indexed)

    def delete(self, ids: list[str]) -> None:
        with self._lock:
            for key in ids:
                self._dense.delete(key)
            self._keyword.delete(ids)

    @staticmethod
    def _filters(filters):
        if any(key.startswith("_") for key in filters):
            raise ValueError("internal index fields cannot be caller filters")
        return {key: list(values) for key, values in filters.items()}

    def vector_search(self, vector, limit, filters):
        if any(not values for values in filters.values()):
            return []
        with self._lock.read(self.read_timeout_seconds):
            return self._dense.search(
                vector,
                top_k=limit,
                filters=self._filters(filters),
                **self._query_options,
            )

    def keyword_search(self, query, limit, filters):
        if any(not values for values in filters.values()):
            return []
        with self._lock.read(self.read_timeout_seconds):
            return self._keyword.search(
                query,
                limit,
                self._filters(filters),
                **self._query_options,
            )

    def point_counts(self, index_key: str = "") -> dict[str, int]:
        """Count actual payload versions in this runtime's two collections."""
        counts = {}
        with self._lock.read(self.read_timeout_seconds):
            for name, store in (("vector", self._dense), ("keyword", self._keyword)):
                if not index_key:
                    counts[f"{name}_points"] = store._client.count(
                        store.collection_name, exact=True, **self._query_options
                    ).count
                    continue
                condition = models.FieldCondition(
                    key="index_key", match=models.MatchValue(value=index_key)
                )
                matching = store._client.count(
                    store.collection_name,
                    exact=True,
                    count_filter=models.Filter(must=[condition]),
                    **self._query_options,
                ).count
                mismatched = store._client.count(
                    store.collection_name,
                    exact=True,
                    count_filter=models.Filter(must_not=[condition]),
                    **self._query_options,
                ).count
                counts[f"{name}_points"] = matching + mismatched
                counts[f"{name}_matching_points"] = matching
                counts[f"{name}_mismatched_points"] = mismatched
        return counts

    def read_source_points(self, source_id: str) -> dict[str, list[VectorPoint]]:
        """Inspect actual content without trusting the local applied checkpoint."""
        result = {}
        with self._lock.read(self.read_timeout_seconds):
            for name, store, id_field in (
                ("vector", self._dense, "_external_id"),
                ("keyword", self._keyword, "_keyword_external_id"),
            ):
                points, offset = [], None
                condition = models.Filter(
                    should=[
                        models.FieldCondition(
                            key="source_id", match=models.MatchValue(value=source_id)
                        ),
                        models.FieldCondition(
                            key=id_field, match=models.MatchValue(value=source_id)
                        ),
                    ]
                )
                while True:
                    records, offset = store._client.scroll(
                        store.collection_name,
                        scroll_filter=condition,
                        offset=offset,
                        limit=100,
                        with_payload=True,
                        with_vectors=name == "vector",
                        **self._query_options,
                    )
                    for record in records:
                        payload = record.payload or {}
                        points.append(
                            VectorPoint(
                                id=payload.get(id_field, str(record.id)),
                                payload=payload,
                                vector=record.vector if name == "vector" else [],
                            )
                        )
                    if offset is None:
                        break
                result[name] = sorted(points, key=lambda point: point.id)
        return result

    def get_vector_ids(self):
        """Snapshot IDs for recovery cleanup; never queried by online retrieval."""
        with self._lock.read(self.read_timeout_seconds):
            return sorted(
                set(self._dense.get_vector_ids()) | set(self._keyword.get_vector_ids())
            )

    def close(self):
        with self._lock:
            try:
                self._keyword.close()
            finally:
                self._dense.close()
