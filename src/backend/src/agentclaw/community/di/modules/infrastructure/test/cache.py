"""Cache concern — test / singlebox binding (in-process memory cache)."""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin


logger = get_logger()


class TestCacheModule(Module):
    """test / singlebox: in-process memory cache."""

    @singleton
    @provider
    def cache(self) -> CachePlugin:
        from agentclaw.community.plugins.local.cache import MemoryCachePlugin

        logger.info("CachePlugin: MemoryCachePlugin (test)")
        return MemoryCachePlugin()
