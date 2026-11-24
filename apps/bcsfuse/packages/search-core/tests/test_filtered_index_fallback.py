from dataclasses import replace
from types import SimpleNamespace

import pytest
from search_core.api.models import (
    RetrievalRequest,
    SearchCommand,
    SearchItem,
    SearchResult,
    VerifiedIdentity,
)
from search_core.application.catalog_retrieval import CatalogRetrieval
from search_core.application.default_candidates import FilteredIndexFallback
from search_core.application.search import CatalogSearchApplication


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "expected"),
    [(1, ["valid"]), (5, ["valid", "later"]), (20, ["valid", "later"])],
)
async def test_empty_after_final_eligibility_and_missing_card(limit, expected):
    valid = {"missing", "retired", "valid", "later"}
    filters = {"labels.visibility": ("public",)}
    req = SearchCommand("miss", limit, "search", filters)
    identity = VerifiedIdentity("test")
    seen = []

    class Normal:
        async def retrieve(self, request):
            return SearchResult((SearchItem("hidden", "", "", 1, "rrf"),))

    class Execution:
        async def call(self, reader, original_filters, limit):
            seen.append((original_filters, limit))
            return reader()

    class Metadata:
        async def enrich(self, result, *, allow_timeout_projection):
            assert not allow_timeout_projection
            assert [i.item_id for i in result.items] == [
                "missing",
                "retired",
                "valid",
                "later",
            ]
            valid.remove("retired")
            return replace(
                result, items=result.items[1:], reasons=("card_metadata_missing",)
            )

    scope = SimpleNamespace(eligible=lambda key, f: key in valid and f == filters)
    fallback = FilteredIndexFallback(
        lambda: ["hidden", "missing", "retired", "valid", "later"],
        Execution(),
        scope,
        lambda: SimpleNamespace(retrieval=Metadata()),
    )
    service = CatalogSearchApplication(
        CatalogRetrieval(Normal(), scope), empty_search_fallback=fallback
    )
    assert not (
        await service.execute(replace(req, operation="recommend"), identity)
    ).items
    result = await service.execute(req, identity)
    assert seen == [(filters, 5)]
    assert [i.item_id for i in result.items] == expected
    assert result.reasons == ("card_metadata_missing", "default_candidates")


@pytest.mark.asyncio
async def test_unavailable_fallback_preserves_empty_result_and_observed_reasons():
    class Timeout:
        async def call(self, *args):
            raise TimeoutError

    fallback = FilteredIndexFallback(None, Timeout(), None, None)
    original = SearchResult(reasons=("vector_disabled",))
    result = await fallback(RetrievalRequest("q", 5, {}), original)
    assert not result.items and result.status == "degraded"
    assert result.reasons == ("vector_disabled", "default_candidates_unavailable")
