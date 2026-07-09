"""Secret concern — community binding.

Capability: secret store. B3 binds the env-var ``CommunitySecretResolver``.
The ``CommunitySecretConfig`` provider lives here (community-only) and reads the
``secret`` block of ``user_config`` — corp/test never resolve it.
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


class CommunitySecretModule(Module):
    """community: env-var secret resolver."""

    @singleton
    @provider
    def secret_config(self) -> cfg.CommunitySecretConfig:
        """Read the ``secret`` block; fall back to dataclass defaults."""
        from agentclaw.community.di.modules.config_module import _block

        block = _block("secret")
        defaults = cfg.CommunitySecretConfig()
        return cfg.CommunitySecretConfig(
            env_prefix=block.get("env_prefix", defaults.env_prefix),
        )

    @singleton
    @provider
    @inject
    def secret_resolver(
        self, config: cfg.CommunitySecretConfig
    ) -> SecretResolver:
        from agentclaw.community.plugins.community.secret_resolver import (
            CommunitySecretResolver,
        )

        return CommunitySecretResolver(env_prefix=config.env_prefix)
