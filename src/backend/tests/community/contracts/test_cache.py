"""Rule 25 conformance — CachePlugin.

Consumer under test: ``MarketCache`` (core/skill_center). It is the
canonical wrapper that fronts ZCache / memory and exercises both the
``set_json`` write path and the ``get_json`` read path.

The plugin-hit assertion is observable: after ``MarketCache.set``, the
key produced by its key builder must appear in the local impl's
``_store`` dict. Without this, a consumer could silently cache only
in its in-memory fallback and the test would still pass.
"""
from __future__ import annotations

from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def test_marketcache_set_then_get_round_trips_through_cache_plugin(world) -> None:
    mc = world.get(MarketCache)
    payload = {"skills": [{"id": "s1", "name": "Test Skill"}]}

    mc.set("market:test", payload)

    # Consumer-level assertion: reading via the consumer returns the payload.
    assert mc.get("market:test") == payload

    # Plugin-hit assertion: the consumer's key (after its prefix logic)
    # must be present in CachePlugin's backing store.
    cache = world.get(CachePlugin)
    full_key = mc._build_key("market:test")
    assert full_key in cache._store, (
        f"CachePlugin._store missing {full_key!r} — "
        f"consumer never routed through the plugin"
    )


def test_marketcache_get_returns_none_on_miss(world) -> None:
    mc = world.get(MarketCache)
    assert mc.get("nope") is None


def test_marketcache_round_trips_through_community_cache(community_world) -> None:
    # Same consumer surface, community CachePlugin (in-process backend by
    # default). Backend-agnostic plugin-hit assertion: the consumer's key is
    # readable back through the community plugin's own get().
    mc = community_world.get(MarketCache)
    payload = {"skills": [{"id": "s1", "name": "Test Skill"}]}
    mc.set("market:community", payload)
    assert mc.get("market:community") == payload

    cache = community_world.get(CachePlugin)
    full_key = mc._build_key("market:community")
    assert cache.get(full_key) is not None, (
        "community CachePlugin must surface the consumer's write"
    )


def test_marketcache_partitions_persisted_skill_data_by_current_tenant(world) -> None:
    mc = world.get(MarketCache)

    with avernet_tenant_scope("tenant-a"):
        mc.set("market_skills_list_default", {"skills": ["tenant-a"]})

    with avernet_tenant_scope("tenant-b"):
        assert mc.get("market_skills_list_default") is None
        mc.set("market_skills_list_default", {"skills": ["tenant-b"]})

    with avernet_tenant_scope("tenant-a"):
        assert mc.get("market_skills_list_default") == {"skills": ["tenant-a"]}
