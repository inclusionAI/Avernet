"""Native BM25 must be used explicitly; Local cannot silently change algorithms."""

import os
from uuid import uuid4

import pytest
from qdrant_client import models
from search_core.config.qdrant import QdrantConnection
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.keyword import QdrantKeywordIndex


def test_local_keyword_rejects_unsupported_multilingual_bm25():
    with pytest.raises(ValueError, match="Qdrant Server.*Local"):
        QdrantKeywordIndex()


@pytest.mark.parametrize("grpc", [False, True])
def test_native_bm25_matches_server_scores_and_omits_index_payload(grpc, monkeypatch):
    url = os.getenv("QDRANT_TEST_URL")
    if not url:
        pytest.skip("QDRANT_TEST_URL is required for the native Server contract")
    index = QdrantKeywordIndex(
        connection=QdrantConnection(
            mode="server",
            url=url,
            prefer_grpc=grpc,
            grpc_port=int(os.getenv("QDRANT_TEST_GRPC_PORT", "6334")),
        ),
        collection_name="bm25_" + uuid4().hex,
        exact_match_field="_exact_id",
        hit_payload_fields=("name", "search_text"),
    )
    try:
        index.upsert(
            [
                VectorPoint(
                    id="short",
                    vector=[],
                    payload={
                        "name": "aliuct",
                        "_exact_id": "lookup-key",
                        "search_text": "支付查询",
                        "visible": True,
                    },
                ),
                VectorPoint(
                    id="long",
                    vector=[],
                    payload={
                        "name": "UCT20",
                        "search_text": "支付查询 " + "天气 " * 80,
                        "visible": True,
                    },
                ),
            ]
        )
        query_points, scroll = index._client.query_points, index._client.scroll
        ranked_payloads, exact_payloads = [], []

        def capture_query(*args, **kwargs):
            response = query_points(*args, **kwargs)
            ranked_payloads.extend(point.payload for point in response.points)
            return response

        def capture_scroll(*args, **kwargs):
            points, offset = scroll(*args, **kwargs)
            exact_payloads.extend(point.payload for point in points)
            return points, offset

        monkeypatch.setattr(index._client, "query_points", capture_query)
        monkeypatch.setattr(index._client, "scroll", capture_scroll)
        hits = index.search("支付", 10, {"visible": True})
        assert [h.id for h in hits] == ["short", "long"]
        raw = query_points(
            index.collection_name,
            query=models.TextQuery(text="支付"),
            using="_bm25_text",
            limit=10,
        ).points
        assert [h.score for h in hits] == pytest.approx([p.score for p in raw])
        assert {h.id for h in index.search("uct", 10, {})} == {"short", "long"}
        exact = index.search("lookup-key", 10, {"visible": True})
        assert [hit.id for hit in exact] == ["short"]
        assert exact[0].payload == {
            "name": "aliuct",
            "search_text": "支付查询",
            "_keyword_exact_id": True,
        }
        assert not index.search("lookup-key", 10, {"visible": False})
        assert ranked_payloads and exact_payloads
        assert all(
            set(payload) == {"_keyword_external_id", "name", "search_text"}
            for payload in [*ranked_payloads, *exact_payloads]
        )
        index.upsert(
            [
                VectorPoint(
                    id="short",
                    vector=[],
                    payload={"name": "天气", "search_text": "天气", "visible": False},
                )
            ]
        )
        assert [h.id for h in index.search("uct", 10, {})] == ["long"]
        index.delete(["long"])
        assert index.search("uct", 10, {}) == []
    finally:
        if index._client is not None:
            index._client.delete_collection(index.collection_name)
        index.close()


def test_existing_sparse_collection_is_reprojected_without_changing_ids():
    from uuid import NAMESPACE_URL, uuid5

    from tests.qdrant_support import server_options

    index = QdrantKeywordIndex(**server_options())
    from search_core.providers.qdrant_server import remote_client

    client = remote_client(index.connection)
    try:
        client.create_collection(
            index.collection_name,
            vectors_config={},
            sparse_vectors_config={
                "text": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        client.upsert(
            index.collection_name,
            [
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, "card")),
                    vector={"text": models.SparseVector(indices=[7], values=[1.0])},
                    payload={"_keyword_external_id": "card", "content": "旧资料"},
                )
            ],
            wait=True,
        )
        index.upsert(
            [
                VectorPoint(
                    id="card",
                    vector=[],
                    payload={"name": "账单助手", "search_text": "查询支付账单"},
                )
            ]
        )
        assert index.get_vector_ids() == ["card"]
        assert [h.id for h in index.search("支付", 10, {})] == ["card"]
        assert not index.search("旧资料", 10, {})
        index.close()
        assert [h.id for h in index.search("账单", 10, {})] == ["card"]
    finally:
        client.delete_collection(index.collection_name)
        client.close()
        index.close()
