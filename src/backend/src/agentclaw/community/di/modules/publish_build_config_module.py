"""Strict configuration binding for the publish-build resource policy."""

from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.core.service_bot.services.bot_build_policy import (
    PublishBuildPolicyConfig,
)
from agentclaw.community.di.modules import config_module

_ALLOWED_KEYS = frozenset({"nas_migration_required", "mcp_catalog_required"})


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


def _as_required_bool(raw: Any, key: str) -> bool:
    """Strict boolean — refuse to guess (sibling of desktop_skill_recovery's
    ``_as_positive_int``).

    这是发布行为开关：空值 ``nas_migration_required:``（None）静默取默认
    会让 required 部署无声跳过迁移（隐藏坏挂载）；字符串 ``"false"`` 因
    真值语义反向把宽容部署变响亮失败。两者都会在配置加载期即失败。
    """
    if isinstance(raw, bool):
        return raw
    raise ValueError(
        f"publish_build.{key} must be a boolean, got {raw!r} "
        f"({type(raw).__name__})"
    )


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
        unknown = sorted(str(key) for key in block if key not in _ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                "unknown publish_build key(s): " + ", ".join(repr(key) for key in unknown)
            )
        defaults = PublishBuildPolicyConfig()
        return PublishBuildPolicyConfig(
            nas_migration_required=_as_required_bool(
                block.get("nas_migration_required", defaults.nas_migration_required),
                "nas_migration_required",
            ),
            mcp_catalog_required=_as_required_bool(
                block.get("mcp_catalog_required", defaults.mcp_catalog_required),
                "mcp_catalog_required",
            ),
        )


__all__ = ["PublishBuildConfigModule"]