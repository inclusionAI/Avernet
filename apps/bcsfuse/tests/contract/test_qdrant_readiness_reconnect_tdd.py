"""Qdrant transport recovery must not remain false-negative after an outage."""

from __future__ import annotations

from typing import Any

from search_core.config.qdrant import QdrantConnection

from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)


class RecoveringClient:
    def __init__(self, control: "RecoveringStore") -> None:
        self._control = control
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get_collection(self, collection_name: str) -> None:
        if not self._control.recovered:
            raise RuntimeError("qdrant is restarting")


class RecoveringStore:
    def __init__(self, *, label: str, connection: QdrantConnection) -> None:
        self.connection = connection
        self.label = label
        self.collection_name = f"{label}_collection"
        self.recovered = False
        self.clients: list[RecoveringClient] = []
        self._client: RecoveringClient | None = None
        self._collection_initialized = False

    def _ensure_client(self) -> None:
        if not self._collection_initialized:
            self._client = RecoveringClient(self)
            self.clients.append(self._client)
            self._collection_initialized = True

    def _ensure_collection(self) -> None:
        self._ensure_client()

    def close(self) -> None:
        if self._client is not None:
            self._client.closed = True
        self._client = None
        self._collection_initialized = False

    def reset_connection(self) -> None:
        """Adopt the server transport reset contract used by readiness probes."""
        self.close()

    @property
    def _client_attribute(self) -> RecoveringClient | None:
        return self._client


def store_with_recovering_transports() -> tuple[
    QdrantMySQLVectorStore, RecoveringStore, RecoveringStore
]:
    dense_connection = QdrantConnection(
        mode="server", url="http://dense-qdrant.test:6333"
    )
    keyword_connection = QdrantConnection(
        mode="server", url="http://keyword-qdrant.test:7333"
    )
    vector_store = QdrantMySQLVectorStore(
        collection_name="readiness_reconnect",
        dimension=2,
        persistence_backend=object(),
        connection=dense_connection,
        keyword_connection=keyword_connection,
    )
    dense = RecoveringStore(label="dense", connection=dense_connection)
    keyword = RecoveringStore(label="keyword", connection=keyword_connection)
    vector_store._qdrant = dense
    vector_store._keywords = keyword
    return vector_store, dense, keyword


def test_failed_dense_probe_resets_its_transport_for_recovery() -> None:
    vector_store, dense, _ = store_with_recovering_transports()
    try:
        first = vector_store.index_dependency_readiness()
        assert first["dense_qdrant_ready"] is False

        assert dense.clients[0].closed is True
        assert dense._client is None

        dense.recovered = True
        second = vector_store.index_dependency_readiness()
        assert second["dense_qdrant_ready"] is True
        assert len(dense.clients) == 2
        assert dense.clients[1].closed is False
    finally:
        vector_store.close()


def test_failed_keyword_probe_resets_only_keyword_transport() -> None:
    vector_store, dense, keyword = store_with_recovering_transports()
    try:
        # Keep dense healthy while keyword is unavailable.
        dense.recovered = True

        first = vector_store.index_dependency_readiness()
        assert first["dense_qdrant_ready"] is True
        assert first["keyword_qdrant_ready"] is False

        assert keyword.clients[0].closed is True
        assert dense.clients[0].closed is False

        keyword.recovered = True
        second = vector_store.index_dependency_readiness()
        assert second["keyword_qdrant_ready"] is True
    finally:
        vector_store.close()


def test_readiness_reset_closes_an_unhealthy_client_before_the_next_probe() -> None:
    vector_store, dense, keyword = store_with_recovering_transports()
    try:
        assert vector_store.index_dependency_readiness() == {
            "dense_qdrant_ready": False,
            "keyword_qdrant_ready": False,
            "dense_qdrant_unavailable": True,
            "keyword_qdrant_unavailable": True,
        }
        assert {client.closed for client in dense.clients} == {True}
        assert {client.closed for client in keyword.clients} == {True}

        dense.recovered = True
        keyword.recovered = True
        assert vector_store.index_dependency_readiness() == {
            "dense_qdrant_ready": True,
            "keyword_qdrant_ready": True,
            "dense_qdrant_unavailable": False,
            "keyword_qdrant_unavailable": False,
        }
    finally:
        vector_store.close()


def test_dependency_readiness_probes_dense_and_keyword_clusters_in_parallel() -> None:
    """Parallel probes keep a dual-start timeout below two client timeouts."""
    from time import perf_counter, sleep

    class SlowProbeClient:
        def get_collection(self, collection_name: str) -> None:
            sleep(0.12)

    class SlowServerStore:
        collection_name = "slow_readiness_collection"

        def __init__(self) -> None:
            self._client = SlowProbeClient()
            self._collection_initialized = True

        def _ensure_client(self) -> None:
            return None

        def _ensure_collection(self) -> None:
            return None

        def close(self) -> None:
            return None

        def reset_connection(self) -> None:
            return None

    vector_store, _, _ = store_with_recovering_transports()
    vector_store._qdrant = SlowServerStore()
    vector_store._keywords = SlowServerStore()

    started = perf_counter()
    try:
        result = vector_store.index_dependency_readiness()
    finally:
        vector_store.close()
    elapsed = perf_counter() - started

    assert result == {
        "dense_qdrant_ready": True,
        "keyword_qdrant_ready": True,
        "dense_qdrant_unavailable": False,
        "keyword_qdrant_unavailable": False,
    }
    # Two sequential 120ms probes fail this with approximately 240ms.
    assert elapsed < 0.20
