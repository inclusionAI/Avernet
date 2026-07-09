"""Unit tests for the community CommunityCache (B3).

Parametrized over both backends: a high-fidelity ``fakeredis`` (exercises the
SET NX / Lua-or-fallback lock path) and the in-process fallback.
"""
from __future__ import annotations

import fakeredis
import pytest
import redis as real_redis

import agentclaw.community.plugins.community.cache as cache_mod
from agentclaw.community.plugins.community.cache import CommunityCache


@pytest.fixture(params=["inproc", "redis"])
def cache(request, monkeypatch) -> CommunityCache:
    if request.param == "inproc":
        return CommunityCache("")
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(
        real_redis.Redis, "from_url", lambda url, **kw: fake
    )
    return CommunityCache("redis://fake")


# ── KV ───────────────────────────────────────────────────────────────────────

def test_set_get_roundtrip(cache):
    assert cache.set("k", "v") is True
    assert cache.get("k") == "v"


def test_delete(cache):
    cache.set("k", "v")
    assert cache.delete("k") is True
    assert cache.get("k") is None


def test_get_missing_returns_none(cache):
    assert cache.get("nope") is None


def test_json_roundtrip(cache):
    assert cache.set_json("k", {"a": 1, "b": [2, 3]}) is True
    assert cache.get_json("k") == {"a": 1, "b": [2, 3]}


def test_get_json_on_corrupt_value_returns_none(cache):
    cache.set("k", "not-json")
    assert cache.get_json("k") is None


# ── Lock ─────────────────────────────────────────────────────────────────────

def test_acquire_then_held_returns_none(cache):
    token = cache.acquire_lock("L")
    assert token is not None
    # Second acquisition while held returns None.
    assert cache.acquire_lock("L") is None


def test_release_with_correct_token_frees_lock(cache):
    token = cache.acquire_lock("L")
    assert cache.release_lock("L", token) is True
    # Freed — re-acquire succeeds.
    assert cache.acquire_lock("L") is not None


def test_release_with_wrong_token_fails_and_keeps_lock(cache):
    token = cache.acquire_lock("L")
    assert cache.release_lock("L", "not-the-token") is False
    # The real holder's lock is intact — re-acquire still blocked.
    assert cache.acquire_lock("L") is None
    assert cache.release_lock("L", token) is True


# ── In-process TTL (deterministic clock) ─────────────────────────────────────

def test_in_proc_ttl_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache_mod.time, "time", lambda: clock["t"])
    cache = CommunityCache("")  # in-process backend
    cache.set("k", "v", ttl=10)
    assert cache.get("k") == "v"
    clock["t"] += 11  # advance past expiry
    assert cache.get("k") is None


def test_in_proc_lock_ttl_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache_mod.time, "time", lambda: clock["t"])
    cache = CommunityCache("")
    assert cache.acquire_lock("L", ttl=30) is not None
    assert cache.acquire_lock("L", ttl=30) is None  # held
    clock["t"] += 31  # lock TTL elapsed
    assert cache.acquire_lock("L", ttl=30) is not None  # re-acquirable


# ── Redis infrastructure failure ─────────────────────────────────────────────

def test_redis_unreachable_degrades_without_raising(monkeypatch):
    class _Broken:
        def get(self, *a, **k):
            raise real_redis.ConnectionError("unreachable")

        def set(self, *a, **k):
            raise real_redis.ConnectionError("unreachable")

        def delete(self, *a, **k):
            raise real_redis.ConnectionError("unreachable")

        def eval(self, *a, **k):
            raise real_redis.ConnectionError("unreachable")

    monkeypatch.setattr(real_redis.Redis, "from_url", lambda url, **k: _Broken())
    cache = CommunityCache("redis://down")
    # Every op degrades to falsy/None on infra failure — never raises.
    assert cache.get("k") is None
    assert cache.set("k", "v") is False
    assert cache.set("k", "v", ttl=10) is False  # the ex= branch also swallows
    assert cache.delete("k") is False
    assert cache.acquire_lock("L") is None  # NOT misread as "held"
    assert cache.release_lock("L", "tok") is False  # eval+fallback both fail
    assert cache.get_json("k") is None
    assert cache.set_json("k", {"a": 1}) is False


def test_set_json_non_serializable_returns_false(cache):
    # A value json.dumps can't serialize is swallowed to False (not raised).
    assert cache.set_json("k", {"bad": object()}) is False


def test_redis_release_lock_paths(monkeypatch):
    # Drive the Redis release_lock branches explicitly against fakeredis.
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(real_redis.Redis, "from_url", lambda url, **k: fake)
    cache = CommunityCache("redis://fake")
    token = cache.acquire_lock("L")
    assert cache.release_lock("L", "wrong-token") is False  # mismatch → kept
    assert cache.acquire_lock("L") is None                  # still held
    assert cache.release_lock("L", token) is True           # holder releases
    assert cache.acquire_lock("L") is not None              # now free
