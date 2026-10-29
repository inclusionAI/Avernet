"""The independently installed providers execute without importing TC."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from search_core.api.errors import SearchUnavailable
from search_core.api.models import RetrievalRequest
from search_core.config.embedding import EmbeddingSettings
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.reranker import RerankResult
from search_core.plugin_api.vector_point import VectorPoint
from search_core.plugin_api.vector_search_hit import VectorSearchHit
from search_core.providers.embedding import RealEmbeddingProvider
from search_core.providers.keyword import QdrantKeywordIndex
from search_core.providers.qdrant_index import QdrantRetrievalIndex
from search_core.providers.reranker import HttpReranker


def test_embedding_actual_http_payload_and_config(monkeypatch):
    seen = []

    def respond(request):
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"embedding": [0.25] * 64}]})

    settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-token",
        model="example-embedding",
        dimension=64,
    )
    provider = RealEmbeddingProvider(
        settings=settings, request_interval_ms=0, max_retries=0
    )
    provider._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert provider.embed("支付查询") == [0.25] * 64
        assert seen[0]["model"] == "example-embedding"
        assert seen[0]["input"] == ["支付查询"]
    finally:
        provider._client.close()


@pytest.mark.parametrize("model", ["bge-reranker-v2-m3", "Qwen3-Reranker-4B"])
def test_reranker_wire_and_order(monkeypatch, model):
    sent = []

    def post(url, **kwargs):
        sent.append(kwargs["json"])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ],
            },
        )

    monkeypatch.setattr("requests.post", post)
    provider = HttpReranker(
        base_url="https://models.example.test/v1",
        api_key="test-token",
        model=model,
    )
    result = provider.rerank(
        "支付", [{"id": "a", "text": "支付"}, {"id": "b", "text": "账单"}], 2
    )
    assert [r.candidate_id for r in result] == ["b", "a"]
    assert sent[0]["model"] == model
    assert ("<|im_start|>" in sent[0]["query"]) == model.startswith("Qwen")


def test_generic_keyword_does_not_scroll_for_worker_id(monkeypatch):
    index = QdrantKeywordIndex()
    try:
        index.upsert(
            [
                VectorPoint(
                    id="a",
                    vector=[],
                    payload={"name": "支付查询", "worker_id": "worker-only-id"},
                )
            ]
        )
        monkeypatch.setattr(
            index._client,
            "scroll",
            Mock(side_effect=AssertionError("business ID lookup")),
        )
        assert index.search("worker-only-id", 2, {}) == []
        assert index.search("支付", 2, {})[0].id == "a"
    finally:
        index.close()


def test_scope_is_applied_before_top_k(tmp_path):
    index = QdrantRetrievalIndex(path=str(tmp_path), dimension=2)
    try:
        index.upsert(
            [
                VectorPoint(
                    id=str(i),
                    vector=[1, 0],
                    payload={
                        "name": "支付查询",
                        "category": "private" if i else "public",
                    },
                )
                for i in range(8)
            ]
        )
        for method, query in [
            (index.vector_search, [1.0, 0.0]),
            (index.keyword_search, "支付"),
        ]:
            assert [
                h.id
                for h in method(query, 1, {"category": ("public",)}, frozenset({"0"}))
            ] == ["0"]
            assert method(query, 1, {}, frozenset()) == []
            with pytest.raises(ValueError):
                method(query, 1, {"_external_id": ("1",)}, frozenset({"0"}))
    finally:
        index.close()


@pytest.mark.parametrize("invalid", ["dependency", "unauthorized", "duplicate", "nan"])
def test_pipeline_errors_are_not_empty_success(invalid):
    hit = VectorSearchHit(id="a", score=0.5, payload={"content": "支付"})
    index = Mock()
    index.vector_search.return_value = [hit]
    index.keyword_search.return_value = []
    embedding = Mock()
    embedding.embed.return_value = [1.0, 0.0]
    reranker = Mock()
    reranker.rerank.return_value = [RerankResult("a", 0.9)]
    if invalid == "dependency":
        index.vector_search.side_effect = RuntimeError("backend failure")
    elif invalid == "unauthorized":
        reranker.rerank.return_value = [RerankResult("private", 0.9)]
    elif invalid == "duplicate":
        reranker.rerank.return_value = [RerankResult("a", 0.9)] * 2
    else:
        reranker.rerank.return_value = [RerankResult("a", float("nan"))]
    service = HybridRetrievalService(
        index=index, embedding=embedding, reranker=reranker
    )
    with pytest.raises(SearchUnavailable):
        asyncio.run(service.retrieve(RetrievalRequest("支付", 1, {}, frozenset({"a"}))))


def test_same_local_path_supports_distinct_collections(tmp_path):
    from search_core.providers.qdrant import QdrantLocalVectorStore

    first = QdrantLocalVectorStore(
        path=str(tmp_path), collection_name="one", dimension=2
    )
    second = QdrantLocalVectorStore(
        path=str(tmp_path), collection_name="two", dimension=2
    )
    try:
        first.upsert([VectorPoint(id="a", vector=[1.0, 0.0])])
        second.upsert([VectorPoint(id="b", vector=[0.0, 1.0])])
        assert [hit.id for hit in second.search([0.0, 1.0], 1)] == ["b"]
        assert first.get_vector_ids() == ["a"]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("status", [402, 429, 503])
def test_online_embedding_rejection_is_one_call_without_sleep(monkeypatch, status):
    from search_core.core.observation import upstream_status

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "synthetic rejection"})

    def no_sleep(*args):
        raise AssertionError("online call must not sleep or retry")

    monkeypatch.setattr("search_core.providers.embedding.time.sleep", no_sleep)
    provider = RealEmbeddingProvider(
        EmbeddingSettings(
            base_url="https://models.example.test/v1",
            auth_token="test-token",
            model="example-embedding",
            dimension=64,
        ),
        request_interval_ms=0,
        max_retries=0,
    )
    provider._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        for expected in range(1, 3):
            with pytest.raises(Exception) as caught:
                provider.embed("test")
            assert upstream_status(caught.value) == status
            assert len(calls) == expected
    finally:
        provider.close()


@pytest.mark.parametrize("status", [402, 429, 503])
def test_online_reranker_rejection_is_one_call(monkeypatch, status):
    import requests
    from search_core.core.observation import upstream_status

    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        response = requests.Response()
        response.status_code = status
        response.raise_for_status()

    monkeypatch.setattr("requests.post", post)
    provider = HttpReranker(
        base_url="https://models.example.test/v1",
        api_key="test-token",
        model="bge-reranker-v2-m3",
    )
    for expected in range(1, 3):
        with pytest.raises(RuntimeError) as caught:
            provider.rerank("test", [{"id": "a", "text": "test"}], 1)
        assert upstream_status(caught.value) == status
        assert len(calls) == expected
