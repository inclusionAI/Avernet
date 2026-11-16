"""Small process-local TTL/LRU cache; no I/O, background jobs or request coalescing."""

import math
import sys
import time
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import fields, is_dataclass
from threading import Lock

_JSON_ATOMS = {str, int, float, bool, type(None)}


def _copy_value(value, memo=None):
    """Copy JSON containers cheaply; retain deepcopy for every other type.

    Each read still owns its mutable containers. One memo preserves aliases and
    cycles, including references crossing into objects handled by deepcopy.
    Exact types keep custom container subclasses on their original copy path.
    """
    kind = type(value)
    if kind not in (dict, list):
        return deepcopy(value, memo)
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    result = {} if kind is dict else []
    memo[identity] = result
    if kind is dict:
        for key, item in value.items():
            copied_key = key if type(key) in _JSON_ATOMS else deepcopy(key, memo)
            result[copied_key] = (
                item if type(item) in _JSON_ATOMS else _copy_value(item, memo)
            )
    else:
        result.extend(
            item if type(item) in _JSON_ATOMS else _copy_value(item, memo)
            for item in value
        )
    return result


def _weight(value):
    """Conservative retained-object estimate, plus a separate entry-count bound."""
    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        size += sys.getsizeof(getattr(value, "__dict__", {}))
        size += sum(_weight(getattr(value, f.name)) for f in fields(value))
    elif isinstance(value, dict):
        size += sum(_weight(k) + _weight(v) for k, v in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_weight(v) for v in value)
    return size


class MemoryCache:
    """Values are copied at the boundary; callers cannot mutate retained entries.

    clear() invalidates data. retire() also permanently disables this cache,
    preventing old in-flight requests from refilling a replaced DRM snapshot.
    Owners guard read/compute/write against their data revision changes.
    """

    def __init__(
        self,
        *,
        enabled,
        ttl_ms,
        max_entries,
        max_bytes,
        clock=time.monotonic,
        stale_ttl_ms=0,
    ):
        if not math.isfinite(ttl_ms) or ttl_ms <= 0:
            raise ValueError("cache TTL must be finite and positive")
        if not math.isfinite(stale_ttl_ms) or stale_ttl_ms < 0:
            raise ValueError("stale TTL must be finite and nonnegative")
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("cache capacity must be positive")
        self._enabled = enabled
        self._ttl = ttl_ms / 1000
        self._stale_ttl = stale_ttl_ms / 1000
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._clock, self._lock = clock, Lock()
        self._entries = OrderedDict()
        self._bytes = 0
        self._counts = Counter()

    @property
    def enabled(self):
        return self._enabled

    def _remove(self, key):
        _, _, weight, _ = self._entries.pop(key)
        self._bytes -= weight

    def get(self, key):
        return self._get(key, allow_stale=False)[0]

    def get_with_stale(self, key):
        """Return (copy, stale); reads never extend either expiry deadline."""
        return self._get(key, allow_stale=True)

    def _get(self, key, *, allow_stale):
        with self._lock:
            if not self._enabled:
                return None, False
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and entry[0] + self._stale_ttl <= now:
                self._remove(key)
                self._counts["expired"] += 1
                entry = None
            stale = entry is not None and entry[0] <= now
            if entry is None or (stale and not allow_stale):
                self._counts["misses"] += 1
                return None, False
            self._entries.move_to_end(key)
            self._counts["stale_hits" if stale else "hits"] += 1
            value = entry[1]
        return _copy_value(value), stale

    def invalidate(self, keys):
        """Drop changed entries and their dependents, including stale copies."""
        keys = frozenset(keys)
        with self._lock:
            affected = [
                key
                for key, entry in self._entries.items()
                if key in keys or not entry[3].isdisjoint(keys)
            ]
            for key in affected:
                self._remove(key)

    def discard(self, key):
        with self._lock:
            if key in self._entries:
                self._remove(key)

    def put(self, key, value, *, dependencies=()):
        if not self._enabled:
            return
        saved = _copy_value(value)
        dependencies = frozenset(dependencies)
        weight = _weight(key) + _weight(saved) + _weight(dependencies) + 128
        with self._lock:
            if not self._enabled:
                return
            if weight > self._max_bytes:
                self._counts["oversized"] += 1
                return
            if key in self._entries:
                self._remove(key)
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._bytes + weight > self._max_bytes
            ):
                self._remove(next(iter(self._entries)))
                self._counts["evicted"] += 1
            self._entries[key] = (
                self._clock() + self._ttl,
                saved,
                weight,
                dependencies,
            )
            self._bytes += weight

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def retire(self):
        with self._lock:
            self._enabled = False
            self._entries.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            now = self._clock()
            for key in [
                k for k, v in self._entries.items() if v[0] + self._stale_ttl <= now
            ]:
                self._remove(key)
                self._counts["expired"] += 1
            return {
                "enabled": self._enabled,
                "entries": len(self._entries),
                "estimated_bytes": self._bytes,
                **{
                    k: self._counts[k]
                    for k in (
                        "hits",
                        "stale_hits",
                        "misses",
                        "expired",
                        "evicted",
                        "oversized",
                    )
                },
            }
