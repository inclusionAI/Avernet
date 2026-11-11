import asyncio

import pytest

from search_core.api.models import SearchItem, SearchResult
from search_core.application.cache import SearchCaches
from search_core.application.card_metadata import CardMetadataRetrieval
from search_core.application.emergency import SearchCachePolicy


class Repository:
    def __init__(self):
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.batches = []
        self.active = 0
        self.exhausted = 0

    async def get_many_cards(self, ids):
        self.batches.append(tuple(ids))
        if self.active == 10:
            self.exhausted += 1
            raise RuntimeError("connection pool exhausted")
        self.active += 1
        self.entered.set()
        try:
            await self.release.wait()
            return {
                key: {"name": key, "description": "", "deleted": False} for key in ids
            }
        finally:
            self.active -= 1


class Execution:
    async def call(self, function, *args):
        return await function(*args)


class Retrieval:
    async def retrieve(self, ids):
        return SearchResult(
            tuple(SearchItem(key, "", "", 1.0, "keyword") for key in ids)
        )


def setup():
    caches = SearchCaches(
        result_entries=10,
        result_bytes=100000,
        embedding_entries=10,
        embedding_bytes=100000,
    )
    _, _, cache = caches.configure(SearchCachePolicy())
    repository = Repository()
    variants = [
        CardMetadataRetrieval(
            Retrieval(), repository, cache, caches, Execution(), project_card=dict
        )
        for _ in range(2)
    ]
    return caches, cache, repository, variants


@pytest.mark.asyncio
async def test_expired_overlapping_cards_do_not_exhaust_ten_connection_pool(
    monkeypatch,
):
    _, cache, repository, (first, second) = setup()
    now = 0.0
    monkeypatch.setattr(cache, "_clock", lambda: now)
    cache.put("shared", {"name": "old", "description": "", "deleted": False})
    now = 61.0

    leader = asyncio.create_task(first.retrieve(("shared", "a")))
    await repository.entered.wait()
    peers = [asyncio.create_task(second.retrieve(("shared", "b"))) for _ in range(31)]
    await asyncio.sleep(0)
    repository.release.set()
    results = await asyncio.gather(leader, *peers)

    assert repository.exhausted == 0
    assert repository.batches == [("shared", "a"), ("b",)]
    assert all(result.status == "ok" for result in results)
    assert all(result.items[0].name == "shared" for result in results)
    assert [item.item_id for item in results[1].items] == ["shared", "b"]


@pytest.mark.asyncio
async def test_cancelled_request_does_not_cancel_another_cards_waiter():
    caches, _, repository, (first, second) = setup()
    leader = asyncio.create_task(first.retrieve(("shared",)))
    await repository.entered.wait()
    peer = asyncio.create_task(second.retrieve(("shared",)))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    repository.release.set()
    result = await peer
    assert [item.name for item in result.items] == ["shared"]
    assert repository.batches == [("shared",)]
    assert not caches.pending_card_metadata


@pytest.mark.asyncio
async def test_catalog_change_prevents_joining_or_refilling_an_old_load(monkeypatch):
    caches, cache, repository, (first, second) = setup()
    old_started, release_old = asyncio.Event(), asyncio.Event()

    async def load(ids):
        if not old_started.is_set():
            old_started.set()
            await release_old.wait()
            name = "old"
        else:
            name = "new"
        return {key: {"name": name, "description": "", "deleted": False} for key in ids}

    monkeypatch.setattr(repository, "get_many_cards", load)
    old = asyncio.create_task(first.retrieve(("shared",)))
    await old_started.wait()
    with caches.changing():
        pass
    fresh = await asyncio.wait_for(second.retrieve(("shared",)), 1)
    release_old.set()
    await old
    assert fresh.items[0].name == cache.get("shared")["name"] == "new"
