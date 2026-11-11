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
    assert index.point_counts() == {"vector_points": 0, "keyword_points": 0}
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
    assert index.point_counts() == {"vector_points": 2, "keyword_points": 2}
    for hits in [
        index.vector_search([1.0, 0.0], 1, {"team": ("b",)}),
        index.keyword_search("invoice", 1, {"team": ("b",)}),
    ]:
        assert [hit.id for hit in hits] == ["b"]
    index.upsert(
        [
            VectorPoint(
                id="b", vector=[0.0, 1.0], payload={"content": "weather", "team": "c"}
            )
        ]
    )
    assert index.keyword_search("invoice", 2, {"team": ("b",)}) == []
    assert [h.id for h in index.keyword_search("weather", 2, {})] == ["b"]
    index.delete(["b"])
    assert index.point_counts() == {"vector_points": 1, "keyword_points": 1}
    assert index.vector_search([0.0, 1.0], 2, {"team": ("b",)}) == []
    assert index.keyword_search("weather", 2, {}) == []


def test_inspection_reads_actual_source_points_and_excludes_other_cards(index):
    index.upsert(
        [
            VectorPoint(
                id="a",
                vector=[1.0, 0.0],
                payload={
                    "source_id": "a",
                    "index_key": "old",
                    "search_text": "invoice",
                },
            ),
            VectorPoint(
                id="a-extra",
                vector=[0.0, 1.0],
                payload={"source_id": "a", "search_text": "bill"},
            ),
            VectorPoint(
                id="b",
                vector=[1.0, 0.0],
                payload={"source_id": "b", "index_key": "new"},
            ),
        ]
    )
    result = index.read_source_points("a")
    for kind in ("vector", "keyword"):
        assert [point.id for point in result[kind]] == ["a", "a-extra"]
        assert result[kind][0].payload["index_key"] == "old"
        assert "index_key" not in result[kind][1].payload
    assert result["vector"][0].vector == [1.0, 0.0]
    index.delete(["a", "a-extra"])
    assert index.read_source_points("a") == {"vector": [], "keyword": []}


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
    assert index.vector_search([1.0, 0.0], 1, {})[0].id == "a"
    assert index.keyword_search("支付", 1, {})[0].id == "a"
    with pytest.raises(ValueError, match="dimension/distance"):
        QdrantRetrievalIndex(
            path=str(tmp_path / "unused"),
            dimension=3,
            collection_name=index._dense.collection_name,
            connection=index.connection,
        )
    assert index.keyword_search("支付", 1, {})[0].id == "a"


def test_keyword_field_indexes_are_created_for_both_server_collections(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import search_core.providers.qdrant_index as module
    from qdrant_client import models

    stores = []
    for name in ("test", "test_keywords"):
        client = Mock()
        client.get_collection.return_value = SimpleNamespace(payload_schema={})
        store = Mock(collection_name=name, _client=client)
        stores.append(store)
    monkeypatch.setattr(module, "dense_store", lambda **kw: stores[0])
    monkeypatch.setattr(module, "QdrantKeywordIndex", lambda **kw: stores[1])
    index = QdrantRetrievalIndex(
        path=str(tmp_path),
        dimension=2,
        collection_name="test",
        connection=QdrantConnection(mode="server", url="http://127.0.0.1:6333"),
        keyword_index_fields=frozenset({"kind"}),
    )
    try:
        for store in stores:
            store._client.create_payload_index.assert_called_once_with(
                collection_name=store.collection_name,
                field_name="kind",
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
    finally:
        index.close()


def test_counts_read_collections_independently_and_propagate_failure(
    index, monkeypatch
):
    index.upsert(
        [VectorPoint(id="a", vector=[1.0, 0.0], payload={"content": "invoice"})]
    )
    index._keyword.delete(["a"])
    assert index.point_counts() == {"vector_points": 1, "keyword_points": 0}

    def unavailable(*args, **kwargs):
        raise OSError("count unavailable")

    monkeypatch.setattr(index._keyword._client, "count", unavailable)
    with pytest.raises(OSError, match="count unavailable"):
        index.point_counts()


def test_version_counts_include_wrong_and_missing_keys_in_both_collections(index):
    index.upsert(
        [
            VectorPoint(id="same", vector=[1.0, 0.0], payload={"index_key": "v2"}),
            VectorPoint(id="old", vector=[1.0, 0.0], payload={"index_key": "v1"}),
            VectorPoint(id="missing", vector=[1.0, 0.0], payload={}),
        ]
    )
    assert index.point_counts("v2") == {
        "vector_points": 3,
        "vector_matching_points": 1,
        "vector_mismatched_points": 2,
        "keyword_points": 3,
        "keyword_matching_points": 1,
        "keyword_mismatched_points": 2,
    }
    index._keyword.delete(["old"])
    counts = index.point_counts("v2")
    assert counts["vector_mismatched_points"] == 2
    assert counts["keyword_mismatched_points"] == 1
