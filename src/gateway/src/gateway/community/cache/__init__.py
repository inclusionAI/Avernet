"""Cache — key-value cache accessor.

The active cache plugin is lazily discovered via entry points on first
access. Community ships an in-memory stub; enterprise delegates to
Layotto ZCache.
"""

from __future__ import annotations

from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.cache.bare import BareCachePlugin
from gateway.community.spi.cache import CachePlugin

_accessor = PluginAccessor[CachePlugin]("gateway.cache", BareCachePlugin)


def get_cache_plugin() -> CachePlugin:
    return _accessor.get()


def set_cache_plugin(plugin: CachePlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_cache_plugin", "set_cache_plugin"]
