"""Expiry refresh is shared, bounded, and separate from explicit invalidation."""

import asyncio

import pytest
from search_core.application.emergency import SearchCachePolicy
from test_card_metadata_coalescing import setup


def value(name):
    return {"name": name, "description": "", "deleted": False}


@pytest.mark.asyncio
async def test_expired_followers_use_old_value_only_during_bounded_refresh(monkeypatch):
    caches, cache, repo, (leader, follower) = setup()
    now = [0.0]
    monkeypatch.setattr(cache, "_clock", lambda: now[0])
    cache.put("a", value("old"))
    now[0] = 60.1
    refresh = asyncio.create_task(leader.retrieve(("a",)))
    await asyncio.wait_for(repo.entered.wait(), 1)
    try:
        peers = await asyncio.wait_for(
            asyncio.gather(*(follower.retrieve(("a",)) for _ in range(32))), 0.5
        )
        assert all(r.items[0].name == "old" for r in peers)
        assert not refresh.done()
        assert repo.batches == [("a",)]
        # Neither reads nor cache stats extend/remove the bounded stale entry.
        assert cache.stats()["entries"] == 1
        now[0] = 64.9
        assert (await follower.retrieve(("a",))).items[0].name == "old"
        now[0] = 65.1
        hard_expired = asyncio.create_task(follower.retrieve(("a",)))
        await asyncio.sleep(0)
        assert not hard_expired.done()
        repo.release.set()
        assert (await refresh).items[0].name == "a"
        assert (await hard_expired).items[0].name == "a"
        assert repo.batches == [("a",)]
        assert not caches.pending_card_metadata
    finally:
        repo.release.set()
        await refresh


@pytest.mark.asyncio
async def test_scoped_change_keeps_unrelated_card_hot_even_during_update():
    caches, cache, repo, (_, service) = setup()
    cache.put("a", value("old a"))
    cache.put("b", value("hot b"))
    cache.put("group", value("old group"), dependencies=("a",))
    caches.candidate_ids.put("list", ("a", "b"))
    with caches.changing(("a",)):
        assert cache.get("a") is None
        assert cache.get("group") is None
        assert caches.candidate_ids.get("list") is None
        hit = await asyncio.wait_for(service.retrieve(("b",)), 0.5)
        assert hit.items[0].name == "hot b"
    assert cache.get("b")["name"] == "hot b"
    assert repo.batches == []


@pytest.mark.asyncio
async def test_explicit_change_does_not_serve_or_refill_stale_refresh(monkeypatch):
    caches, cache, repo, (leader, follower) = setup()
    now = [0.0]
    monkeypatch.setattr(cache, "_clock", lambda: now[0])
    cache.put("a", value("cached old"))
    now[0] = 61
    entered, release = asyncio.Event(), asyncio.Event()

    async def load(ids):
        if not entered.is_set():
            entered.set()
            await release.wait()
            return {"a": value("loaded old")}
        return {"a": value("updated")}

    monkeypatch.setattr(repo, "get_many_cards", load)
    old = asyncio.create_task(leader.retrieve(("a",)))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with caches.changing(("a",)):
            pass
        result = await asyncio.wait_for(follower.retrieve(("a",)), 0.5)
        assert result.items[0].name == "updated"
    finally:
        release.set()
        await old
    assert cache.get("a")["name"] == "updated"


@pytest.mark.asyncio
async def test_missing_refresh_cannot_keep_serving_old_card(monkeypatch):
    _, cache, repo, (leader, follower) = setup()
    now = [0.0]
    monkeypatch.setattr(cache, "_clock", lambda: now[0])
    cache.put("a", value("old"))
    now[0] = 61

    async def missing(ids):
        return {}

    monkeypatch.setattr(repo, "get_many_cards", missing)
    assert not (await leader.retrieve(("a",))).items
    assert cache.get_with_stale("a")[0] is None
    assert not (await follower.retrieve(("a",))).items


@pytest.mark.asyncio
async def test_failed_refresh_does_not_extend_stale_deadline(monkeypatch):
    _, cache, repo, (leader, _) = setup()
    now = [0.0]
    monkeypatch.setattr(cache, "_clock", lambda: now[0])
    cache.put("a", value("old"))
    now[0] = 61

    async def failed(ids):
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(repo, "get_many_cards", failed)
    assert not (await leader.retrieve(("a",))).items
    now[0] = 65.1
    assert cache.get_with_stale("a")[0] is None


def test_grace_only_applies_to_enabled_card_metadata_cache():
    state, cache, _, _ = setup()
    assert cache._stale_ttl == 5
    result, embedding, short = state.configure(
        SearchCachePolicy(card_metadata={"ttl_ms": 100})
    )
    assert short._stale_ttl == 0.1
    assert result._stale_ttl == embedding._stale_ttl == 0
    _, _, disabled = state.configure(SearchCachePolicy(card_metadata={"ttl_ms": 0}))
    assert not disabled.enabled


def test_observed_update_and_delete_preserve_other_metadata(tmp_path):
    from search_core.catalog.cards import CardSnapshot
    from search_core.catalog.updates import CardUpdates
    from search_core.providers.sql_catalog import sqlite_catalog_repository
    from test_catalog_startup import Index, Model

    state, cache, _, _ = setup()
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    updates = CardUpdates(
        repo,
        Model(),
        Index(),
        embedding_key="model/2",
        instance_id="test",
        index_key="default",
        on_change=state.changing,
    )
    for key in ("a", "b"):
        repo.save(
            CardSnapshot(
                agent_id=key,
                source_version="1",
                source_card={"name": key},
                labels={"visibility": ["public"]},
            ),
            0,
        )
    updates.run_once()
    cache.put("a", value("old a"))
    cache.put("b", value("hot b"))
    row = repo.get("a")
    repo.save(
        row.card.model_copy(
            update={
                "source_version": "2",
                "labels": {"visibility": ["private"]},
            }
        ),
        row.revision,
    )
    updates.run_once()
    assert cache.get("a") is None
    assert cache.get("b")["name"] == "hot b"
    assert not updates.eligible("a", {"labels.visibility": ("public",)})
    cache.put("a", value("new a"))
    repo.delete("a", repo.get("a").revision)
    updates.run_once()
    assert cache.get("a") is None
    assert cache.get("b")["name"] == "hot b"


def test_stale_copy_budget_and_retirement():
    from search_core.core.memory_cache import MemoryCache

    now = [0.0]
    cache = MemoryCache(
        enabled=True,
        ttl_ms=1000,
        stale_ttl_ms=1000,
        max_entries=2,
        max_bytes=10000,
        clock=lambda: now[0],
    )
    cache.put("a", {"nested": [1]}, dependencies=("member",))
    now[0] = 1.1
    stale, expired = cache.get_with_stale("a")
    assert expired
    stale["nested"].append(2)
    assert cache.get_with_stale("a")[0] == {"nested": [1]}
    cache.put("b", {})
    cache.put("c", {})
    assert cache.get_with_stale("a")[0] is None
    assert cache.stats()["entries"] == 2
    cache.retire()
    cache.put("late", {})
    assert cache.get_with_stale("b")[0] is None
    assert cache.stats()["estimated_bytes"] == 0
