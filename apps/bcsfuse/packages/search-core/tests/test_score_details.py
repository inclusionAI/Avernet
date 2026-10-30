"""Stage evidence is additive: it must not change selection or ordering."""

import asyncio

import pytest
from search_core.api.models import RetrievalRequest
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.reranker import RerankResult
from test_document_retrieval import hit


class Index:
    def vector_search(self, *args):
        return [
            hit("a1", "a", 0.9, "a"),
            hit("a2", "a", 0.8, "a2"),
            hit("b", "b", 0.0, "b"),
        ]

    def keyword_search(self, *args):
        return [hit("b", "b", 5.0, "b"), hit("a2", "a", 2.0, "a2")]


class Model:
    def embed(self, text):
        return [1.0]


class Ranker:
    def rerank(self, query, candidates, limit):
        return [RerankResult("b", 0.8), RerankResult("a", 0.3)][:limit]


def retrieve(**kwargs):
    service = HybridRetrievalService(
        index=Index(), embedding=Model(), group_by_source=True, **kwargs
    )
    return asyncio.run(service.retrieve(RetrievalRequest("q", 2, {}, None)))


def test_grouped_scores_and_ranks_survive_rerank_without_changing_order():
    result = retrieve(reranker=Ranker())
    assert [i.item_id for i in result.items] == ["b", "a"]
    b, a = result.items
    assert (b.score, b.score_source) == (0.8, "reranker")
    assert (b.score_details.keyword.value, b.score_details.keyword.rank) == (5, 1)
    assert (b.score_details.vector.value, b.score_details.vector.rank) == (0, 2)
    assert b.score_details.rrf.value == pytest.approx(1 / 61 + 1 / 62)
    assert b.score_details.rrf.rank == 2  # Fusion tie is resolved by Agent ID.
    assert (b.score_details.reranker.value, b.score_details.reranker.rank) == (0.8, 1)
    assert (a.score_details.vector.value, a.score_details.vector.rank) == (0.9, 1)
    assert a.score_details.keyword.rank == 2
    assert a.score_details.reranker.rank == 2


def test_rerank_failure_reports_only_real_stage_results():
    class Broken:
        def rerank(self, *args):
            raise TimeoutError()

    result = retrieve(reranker=Broken(), allow_partial=True)
    assert result.reasons == ("reranker_unavailable",)
    assert [i.item_id for i in result.items] == ["a", "b"]
    for item in result.items:
        assert item.score_source == "rrf"
        assert item.score == item.score_details.rrf.value
        assert item.score_details.reranker is None


def test_disabled_and_nonmatching_stages_are_not_zero_scores():
    result = retrieve(vector_enabled=False)
    assert result.items[0].score_details.vector is None
    assert result.items[0].score_details.reranker is None
    result = retrieve(vector_min_score=0.5)
    a, b = result.items
    assert a.score_details.vector is not None
    assert b.score_details.vector is None
    assert b.score_details.keyword.value == 5


def test_embedding_failure_keeps_keyword_score_and_empty_stages():
    class Broken(Model):
        def embed(self, text):
            raise RuntimeError()

    service = HybridRetrievalService(
        index=Index(), embedding=Broken(), group_by_source=True, allow_partial=True
    )
    result = asyncio.run(service.retrieve(RetrievalRequest("q", 2, {}, None)))
    assert "embedding_unavailable" in result.reasons
    assert result.items[0].score_details.keyword.rank == 1
    assert result.items[0].score_details.vector is None
