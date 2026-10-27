"""Run the same retrieval contract against Local, Server HTTP and Server gRPC."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from qdrant_client.http.exceptions import ResponseHandlingException
from search_core.config.qdrant import QdrantConnection
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.qdrant_index import QdrantRetrievalIndex


@pytest.fixture(params=["local", "http", "grpc"])
def index(request, tmp_path):
    mode = request.param
    url = os.getenv("QDRANT_TEST_URL", "")
    if mode != "local" and not url:
        pytest.skip("Set QDRANT_TEST_URL to run the real Server contract")
    connection = (
        QdrantConnection()
        if mode == "local"
        else QdrantConnection(
            mode="server",
            url=url,
            prefer_grpc=mode == "grpc",
            grpc_port=int(os.getenv("QDRANT_TEST_GRPC_PORT", "6334")),
        )
    )
    instance = QdrantRetrievalIndex(
        path=str(tmp_path),
        dimension=2,
        collection_name="contract_" + uuid4().hex,
        connection=connection,
    )
    yield instance
    if mode != "local":
        instance._dense._client.delete_collection(instance._dense.collection_name)
        instance._keyword._client.delete_collection(instance._keyword.collection_name)
    instance.close()


def test_retrieval_filters_updates_and_deletes(index):
    index.upsert(
        [
            VectorPoint(
                id="a",
                vector=[1.0, 0.0],
                payload={"name": "payment", "content": "payment invoice", "team": "a"},
            ),
            VectorPoint(
                id="b",
                vector=[0.9, 0.1],
                payload={"content": "payment invoice", "team": "b"},
            ),
        ]
    )
    for hits in [
        index.vector_search([1.0, 0.0], 1, {"team": ("b",)}, frozenset({"b"})),
        index.keyword_search("invoice", 1, {"team": ("b",)}, frozenset({"b"})),
    ]:
        assert [hit.id for hit in hits] == ["b"]
    index.upsert(
        [
            VectorPoint(
                id="b", vector=[0.0, 1.0], payload={"content": "weather", "team": "c"}
            )
        ]
    )
    assert index.keyword_search("invoice", 2, {}, frozenset({"b"})) == []
    assert [h.id for h in index.keyword_search("weather", 2, {}, None)] == ["b"]
    index.delete(["b"])
    assert index.vector_search([0.0, 1.0], 2, {}, frozenset({"b"})) == []
    assert index.keyword_search("weather", 2, {}, None) == []


def test_server_queries_do_not_serialize(index, monkeypatch):
    if index.connection.mode != "server":
        pytest.skip("Local intentionally serializes access")
    rendezvous = Barrier(2)

    def query(*args, **kwargs):
        rendezvous.wait(timeout=3)
        return []

    monkeypatch.setattr(index._dense, "search", query)
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(index.vector_search, [1.0, 0.0], 1, {}, None) for _ in range(2)
        ]
        assert [f.result() for f in futures] == [[], []]


def test_server_requires_explicit_endpoint_and_rejects_bad_mode():
    with pytest.raises(ValueError):
        QdrantConnection(mode="server")
    with pytest.raises(ValueError):
        QdrantConnection(mode="faiss")


def test_server_connection_failure_does_not_fall_back(tmp_path):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        with pytest.raises(ResponseHandlingException):
            QdrantRetrievalIndex(
                path=str(tmp_path),
                dimension=2,
                connection=QdrantConnection(
                    mode="server", url=url, prefer_grpc=False, timeout=1
                ),
            )


def test_server_retains_both_collections_and_rejects_dimension_change(index, tmp_path):
    if index.connection.mode != "server":
        pytest.skip("Server persists both collections")
    index.upsert(
        [VectorPoint(id="a", vector=[1.0, 0.0], payload={"content": "支付助手"})]
    )
    index._dense.close()
    index._keyword.close()
    assert index.vector_search([1.0, 0.0], 1, {}, None)[0].id == "a"
    assert index.keyword_search("支付", 1, {}, None)[0].id == "a"
    with pytest.raises(ValueError, match="dimension/distance"):
        QdrantRetrievalIndex(
            path=str(tmp_path / "unused"),
            dimension=3,
            collection_name=index._dense.collection_name,
            connection=index.connection,
        )
    assert index.keyword_search("支付", 1, {}, None)[0].id == "a"
