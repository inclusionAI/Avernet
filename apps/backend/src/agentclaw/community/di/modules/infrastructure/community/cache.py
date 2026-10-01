"""Cache concern — community binding.

Capability: distributed cache + lock. B3 binds the ``CommunityCache`` (Redis
when configured, else in-process). The ``CommunityCacheConfig`` provider lives
here (community-only) and reads the ``cache`` block of ``user_config``, with the
``REDIS_URL`` env var taking precedence — corp/test never resolve it.
"""
from __future__ import annotations

import os

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.cache import CachePlugin


class CommunityCacheModule(Module):
    """community: deployable cache + lock (Redis / in-process)."""

    @singleton
    @provider
    def cache_config(self) -> cfg.CommunityCacheConfig:
        """Resolve the Redis URL: ``REDIS_URL`` env wins, else the ``cache``
        block, else the dataclass default (empty ⇒ in-process backend)."""
        from agentclaw.community.di.modules.config_module import _block

        block = _block("cache")
        defaults = cfg.CommunityCacheConfig()
        redis_url = (
            os.environ.get("REDIS_URL")
            or block.get("redis_url")
            or defaults.redis_url
        )
        return cfg.CommunityCacheConfig(redis_url=redis_url)

    @singleton
    @provider
    @inject
    def cache(self, config: cfg.CommunityCacheConfig) -> CachePlugin:
        from agentclaw.community.plugins.community.cache import CommunityCache

        return CommunityCache(config.redis_url)
