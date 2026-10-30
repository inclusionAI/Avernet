import asyncio

import pytest
from search_core.api.models import RetrievalRequest
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.reranker import RerankResult
from search_core.plugin_api.vector_search_hit import VectorSearchHit


def hit(doc, source, score, text):
    return VectorSearchHit(
        id=doc,
        score=score,
        payload={"source_id": source, "search_text": "recall", "rerank_text": text},
    )


def test_documents_group_before_fusion_and_use_representative_text():
    class Index:
        def vector_search(self, vector, limit, filters, allowed):
            assert limit == 60
            return [
                hit("a1", "a", 0.9, "a vector"),
                hit("a2", "a", 0.8, "a duplicate"),
                hit("b1", "b", 0.7, "b text"),
            ]

        def keyword_search(self, *args):
            return [hit("a2", "a", 3, "a keyword"), hit("c1", "c", 0.1, "c text")]

    class Model:
        def embed(self, query):
            return [1.0, 0.0]

    class Ranker:
        def rerank(self, query, candidates, limit):
            assert [c["id"] for c in candidates] == ["a", "b"]
            assert candidates[0]["text"] == "a vector"
            return [RerankResult(c["id"], 1.0) for c in candidates]

    service = HybridRetrievalService(
        index=Index(),
        embedding=Model(),
        reranker=Ranker(),
        group_by_source=True,
        recall_limit=60,
        vector_min_score=0.5,
        keyword_min_score=0.5,
    )
    result = asyncio.run(service.retrieve(RetrievalRequest("query", 5, {}, None)))
    assert [i.item_id for i in result.items] == ["a", "b"]


def test_thresholds_can_return_valid_empty_results_and_are_optional():
    class Index:
        def vector_search(self, *args):
            return [hit("d", "a", 0.1, "body")]

        keyword_search = vector_search

    class Model:
        def embed(self, query):
            return [1.0]

    strict = HybridRetrievalService(
        index=Index(), embedding=Model(), vector_min_score=0.2, keyword_min_score=0.2
    )
    result = asyncio.run(strict.retrieve(RetrievalRequest("q", 1, {}, None)))
    assert result.status == "ok" and not result.items
    legacy = HybridRetrievalService(index=Index(), embedding=Model())
    assert (
        asyncio.run(legacy.retrieve(RetrievalRequest("q", 1, {}, None)))
        .items[0]
        .item_id
        == "d"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_thresholds_reject_invalid_values(value):
    with pytest.raises(ValueError):
        HybridRetrievalService(
            index=object(), embedding=object(), vector_min_score=value
        )
