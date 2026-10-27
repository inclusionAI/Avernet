import asyncio
from threading import Event

from search_core.api.models import RetrievalRequest
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.qdrant_index import QdrantRetrievalIndex


class Embedding:
    def embed(self, text):
        return [1.0, 0.0, 0.0]


def test_real_qdrant_recall_filters_and_restart(tmp_path):
    def open_index():
        return QdrantRetrievalIndex(path=str(tmp_path / "index"), dimension=3)

    index = open_index()
    index.upsert(
        [
            VectorPoint(
                id="pay",
                vector=[1, 0, 0],
                payload={
                    "name": "支付助手",
                    "content": "支付账单查询",
                    "category": "payment",
                },
            ),
            VectorPoint(
                id="private",
                vector=[1, 0, 0],
                payload={
                    "name": "内部支付",
                    "content": "支付账单查询",
                    "category": "private",
                },
            ),
        ]
    )
    index.close()
    index = open_index()
    try:
        service = HybridRetrievalService(
            index=index, embedding=Embedding(), candidate_limit=10
        )
        request = RetrievalRequest(
            "支付", 2, {"category": ("payment",)}, frozenset({"pay"})
        )
        result = asyncio.run(service.retrieve(request))
        assert [x.item_id for x in result.items] == ["pay"]
        assert index.keyword_search("pay", 3, {}, None) == []
        index.delete(["pay"])
        assert asyncio.run(service.retrieve(request)).items == ()
    finally:
        index.close()


def test_keyword_branch_starts_while_embedding_waits():
    keyword_started = Event()

    class BlockingEmbedding:
        def embed(self, text):
            assert keyword_started.wait(2), "keyword incorrectly waits for embedding"
            return [1.0]

    class Index:
        def keyword_search(self, *args):
            keyword_started.set()
            return []

        def vector_search(self, *args):
            return []

    service = HybridRetrievalService(index=Index(), embedding=BlockingEmbedding())
    assert asyncio.run(service.retrieve(RetrievalRequest("x", 1, {}, None))).items == ()


def test_empty_authorization_never_calls_dependencies():
    service = HybridRetrievalService(index=object(), embedding=object())
    assert (
        asyncio.run(service.retrieve(RetrievalRequest("x", 1, {}, frozenset()))).items
        == ()
    )
