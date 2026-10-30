"""Small process-local TTL/LRU cache; no I/O, background jobs or request coalescing."""

import math
import sys
import time
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import fields, is_dataclass
from threading import Lock


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
        self, *, enabled, ttl_ms, max_entries, max_bytes, clock=time.monotonic
    ):
        if not math.isfinite(ttl_ms) or ttl_ms <= 0:
            raise ValueError("cache TTL must be finite and positive")
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("cache capacity must be positive")
        self._enabled = enabled
        self._ttl = ttl_ms / 1000
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._clock, self._lock = clock, Lock()
        self._entries = OrderedDict()
        self._bytes = 0
        self._counts = Counter()

    @property
    def enabled(self):
        return self._enabled

    def _remove(self, key):
        _, _, weight = self._entries.pop(key)
        self._bytes -= weight

    def get(self, key):
        with self._lock:
            if not self._enabled:
                return None
            entry = self._entries.get(key)
            if entry is not None and entry[0] <= self._clock():
                self._remove(key)
                self._counts["expired"] += 1
                entry = None
            if entry is None:
                self._counts["misses"] += 1
                return None
            self._entries.move_to_end(key)
            self._counts["hits"] += 1
            value = entry[1]
        return deepcopy(value)

    def put(self, key, value):
        if not self._enabled:
            return
        saved = deepcopy(value)
        weight = _weight(key) + _weight(saved) + 128
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
            self._entries[key] = (self._clock() + self._ttl, saved, weight)
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
            for key in [k for k, v in self._entries.items() if v[0] <= now]:
                self._remove(key)
                self._counts["expired"] += 1
            return {
                "enabled": self._enabled,
                "entries": len(self._entries),
                "estimated_bytes": self._bytes,
                **{
                    k: self._counts[k]
                    for k in ("hits", "misses", "expired", "evicted", "oversized")
                },
            }
