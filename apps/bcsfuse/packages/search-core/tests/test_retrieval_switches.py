import asyncio

import pytest
from search_core.api.errors import SearchUnavailable
from search_core.api.models import RetrievalRequest
from search_core.core.retrieval import HybridRetrievalService


@pytest.mark.parametrize("keyword,vector", [(True, False), (False, True), (True, True)])
def test_only_enabled_branches_are_called(keyword, vector):
    calls = []

    class Model:
        async def embed(self, query):
            calls.append("embedding")
            return [1.0]

    class Index:
        def keyword_search(self, *args):
            calls.append("keyword")
            return []

        def vector_search(self, *args):
            calls.append("vector")
            return []

    service = HybridRetrievalService(
        index=Index(), embedding=Model(), keyword_enabled=keyword, vector_enabled=vector
    )
    result = asyncio.run(service.retrieve(RetrievalRequest("test", 1, {}, None)))
    assert ("keyword" in calls) == keyword
    assert ("embedding" in calls) == vector
    assert ("vector" in calls) == vector
    assert result.status == ("ok" if keyword and vector else "degraded")


def test_disabled_branch_does_not_mask_failure_of_only_enabled_branch():
    class Index:
        def keyword_search(self, *args):
            raise RuntimeError("offline")

    service = HybridRetrievalService(
        index=Index(), embedding=object(), vector_enabled=False, allow_partial=True
    )
    with pytest.raises(SearchUnavailable):
        asyncio.run(service.retrieve(RetrievalRequest("test", 1, {}, None)))


def test_cannot_disable_all_recall():
    with pytest.raises(ValueError):
        HybridRetrievalService(
            index=object(),
            embedding=object(),
            vector_enabled=False,
            keyword_enabled=False,
        )
