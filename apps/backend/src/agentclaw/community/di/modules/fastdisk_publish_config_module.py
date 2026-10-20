"""DI provider for the fastdisk publish/restore deployment configuration.

The provider deliberately lives outside ``config_module.py`` to keep that shared
module under the architecture file-size cap. It only maps the validated YAML
block into the typed configuration; the runtime script still resolves credentials.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.core.service_bot.services.fastdisk_publish import (
    FastdiskPublishConfig,
)
from agentclaw.community.di.modules import config_module


class FastdiskPublishConfigModule(Module):
    """Bind the ``fastdisk_publish`` user-config block to the typed policy."""

    @singleton
    @provider
    def fastdisk_publish(self) -> FastdiskPublishConfig:
        raw = config_module.read_user_config().get("fastdisk_publish") or {}
        block: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        allowed = {field.name for field in fields(FastdiskPublishConfig)}
        if set(block) - allowed:
            raise ValueError("unknown fastdisk_publish configuration key")
        return FastdiskPublishConfig(**block)


__all__ = ["FastdiskPublishConfigModule"]
