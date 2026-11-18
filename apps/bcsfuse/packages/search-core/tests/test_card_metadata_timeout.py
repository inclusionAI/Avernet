"""Timeout summaries are opt-in and never resurrect confirmed missing Cards."""

import asyncio

import pytest
from search_core.application.emergency import SearchCachePolicy
from test_card_metadata_coalescing import setup


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("fallback", [True, False])
async def test_timeout_projection_is_explicit_and_preserves_valid_cache(
    cached, fallback
):
    state, cache, repository, (service, _) = setup()
    if not cached:
        _, _, cache = state.configure(SearchCachePolicy(enabled=False))
        service.cache = cache
    cache.put("valid", {"name": "current", "description": "", "deleted": False})
    cache.put("deleted", {"name": "gone", "description": "", "deleted": True})
    if fallback:
        service.project_timeout = lambda item: item

    async def load(ids):
        raise TimeoutError

    repository.get_many_cards = load
    result = await service.retrieve(("timeout", "valid", "deleted"))
    expected = (["timeout"] if fallback else []) + (["valid"] if cached else [])
    if not cached and fallback:
        expected = ["timeout", "valid", "deleted"]  # No metadata was known.
    assert [item.item_id for item in result.items] == expected
    assert result.status == "degraded"
    assert set(result.reasons) == {"card_metadata_unavailable", "card_metadata_timeout"}
    assert not state.pending_card_metadata


@pytest.mark.asyncio
async def test_overlapping_timeout_does_not_restore_missing_or_deleted_batch():
    _, _, repository, (leader_service, follower_service) = setup()
    entered, release = asyncio.Event(), asyncio.Event()
    follower_service.project_timeout = lambda item: item

    async def load(ids):
        if "timeout" in ids:
            entered.set()
            await release.wait()
            raise TimeoutError
        return {"deleted": {"name": "", "description": "", "deleted": True}}

    repository.get_many_cards = load
    leader = asyncio.create_task(leader_service.retrieve(("timeout",)))
    await entered.wait()
    follower = asyncio.create_task(
        follower_service.retrieve(("missing", "timeout", "deleted"))
    )
    await asyncio.sleep(0)
    release.set()
    result = await follower
    await leader
    assert [item.item_id for item in result.items] == ["timeout"]


@pytest.mark.asyncio
async def test_non_timeout_failure_does_not_return_summary():
    _, _, repository, (service, _) = setup()
    service.project_timeout = lambda item: item

    async def load(ids):
        raise RuntimeError("database unavailable")

    repository.get_many_cards = load
    result = await service.retrieve(("a",))
    assert not result.items
    assert result.reasons == ("card_metadata_unavailable",)
