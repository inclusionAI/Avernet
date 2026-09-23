"""Strict configuration binding for the publish-build resource policy."""

from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.core.service_bot.services.bot_build_policy import (
    PublishBuildPolicyConfig,
)
from agentclaw.community.di.modules import config_module


def _block() -> dict[str, Any]:
    user_config = config_module.read_user_config()
    if "publish_build" not in user_config:
        return {}
    raw = user_config["publish_build"]
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("publish_build must be a mapping")
    return dict(raw)


class PublishBuildConfigModule(Module):
    """Bind the publish-build policy away from the legacy config monolith.

    Both keys default True (prod semantics: missing NAS staging / empty
    device MCP catalog fail the build loudly). A deployment without those
    resources (e.g. singlebox's local sandboxes) declares False here — the
    build code stays one code path and never probes the host.
    """

    @singleton
    @provider
    def publish_build_policy(self) -> PublishBuildPolicyConfig:
        block = _block()
        defaults = PublishBuildPolicyConfig()
        return PublishBuildPolicyConfig(
            nas_migration_required=block.get(
                "nas_migration_required", defaults.nas_migration_required
            ),
            mcp_catalog_required=block.get(
                "mcp_catalog_required", defaults.mcp_catalog_required
            ),
        )


__all__ = ["PublishBuildConfigModule"]