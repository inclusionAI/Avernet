"""Server transport for existing dense point semantics; no Local fallback."""

from threading import Lock

from qdrant_client import QdrantClient, models

from search_core.config.qdrant import QdrantConnection
from search_core.providers.qdrant import QdrantLocalVectorStore
from search_core.providers.qdrant_ids import _logical_id_from_result
from search_core.providers.qdrant_payload import (
    DENSE_PAYLOAD_INDEX_FIELDS,
    ensure_payload_indexes,
)


def remote_client(connection: QdrantConnection) -> QdrantClient:
    if connection.mode != "server":
        raise ValueError("remote client requires server mode")
    return QdrantClient(
        url=connection.url,
        grpc_port=connection.grpc_port,
        prefer_grpc=connection.prefer_grpc,
        timeout=connection.timeout,
        api_key=connection.api_key or None,
    )


class QdrantServerVectorStore(QdrantLocalVectorStore):
    """Reuse point IDs/payload/filter behavior with a separate Server client."""

    def __init__(
        self,
        *,
        connection: QdrantConnection,
        collection_name: str,
        dimension: int,
        distance: str = "Cosine",
        payload_indexes_on_connect: bool = True,
    ):
        super().__init__(collection_name, "[server]", dimension, distance)
        self.connection = connection
        self._payload_indexes_on_connect = payload_indexes_on_connect
        self._init_lock = Lock()

    def _ensure_client(self):
        if self._collection_initialized:
            return
        with self._init_lock:
            if self._collection_initialized:
                return
            client = remote_client(self.connection)
            try:
                if not client.collection_exists(self.collection_name):
                    client.create_collection(
                        self.collection_name,
                        vectors_config=models.VectorParams(
                            size=self.dimension,
                            distance=self.distance,
                        ),
                    )
                params = client.get_collection(
                    self.collection_name
                ).config.params.vectors
                if not isinstance(params, models.VectorParams) or (
                    params.size != self.dimension or params.distance != self.distance
                ):
                    raise ValueError(
                        "Qdrant collection dimension/distance does not match"
                    )
                if self._payload_indexes_on_connect:
                    ensure_payload_indexes(
                        client,
                        collection_name=self.collection_name,
                        fields=DENSE_PAYLOAD_INDEX_FIELDS,
                    )
            except Exception:
                client.close()
                raise
            self._client = client
            self._collection_initialized = True

    def ensure_payload_indexes(self) -> None:
        """Create the bounded Server filter indexes without changing points."""

        self._ensure_client()
        ensure_payload_indexes(
            self._client,
            collection_name=self.collection_name,
            fields=DENSE_PAYLOAD_INDEX_FIELDS,
        )
    def _ensure_collection(self):
        self._ensure_client()

    def get_vector_ids(self):
        self._ensure_client()
        ids, offset = [], None
        while True:
            points, offset = self._client.scroll(
                self.collection_name,
                offset=offset,
                limit=256,
                with_vectors=False,
            )
            ids.extend(
                _logical_id_from_result(str(p.id), p.payload or {}) for p in points
            )
            if offset is None:
                return ids

    def size(self):
        self._ensure_client()
        return self._client.count(self.collection_name, exact=True).count

    def clear(self):
        self._ensure_client()
        self._client.delete(
            self.collection_name,
            models.FilterSelector(filter=models.Filter()),
            wait=True,
        )

    def close(self):
        with self._init_lock:
            if self._client is not None:
                self._client.close()
            self._client = None
            self._collection_initialized = False

    def reset_connection(self) -> None:
        """Close an unhealthy transport so the next probe builds a fresh client.

        The Qdrant client normally reconnects internally, but an endpoint or
        proxy reset can leave process-local transport state behind. Readiness
        must recover based on the server, not based on a stale cached client.
        """

        self.close()


def dense_store(
    *,
    connection: QdrantConnection,
    collection_name: str,
    path: str,
    dimension: int,
    distance: str = "Cosine",
):
    if connection.mode == "server":
        return QdrantServerVectorStore(
            connection=connection,
            collection_name=collection_name,
            dimension=dimension,
            distance=distance,
        )
    return QdrantLocalVectorStore(
        collection_name=collection_name,
        path=path,
        dimension=dimension,
        distance=distance,
    )
