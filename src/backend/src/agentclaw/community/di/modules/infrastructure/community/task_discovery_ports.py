"""Task-discovery ports — community binding.

- ``ConfigFrontendUrlProvider``: 前端 URL「配置数据」端口（非 plugin）。
  community 从 ``user_config.task_discovery`` 中性块读
  ``frontend_url`` / ``frontend_url_pre`` / ``frontend_url_prod``，按部署 env
  在装配期固化静态值；未配置 → 空值（下游回落构造默认 localhost）。corp 列经
  ``CorpTaskIntegrationModule`` 用钉钉块的同名字段构造同一实现类（YAML 不同、
  实现同一）。
- ``NotifyMessagesProvider``: task_discovery 通知窄端口，仍为 plugin 契约
  （凭证机制两端不同）。community 无钉钉通道 → Null 实现。
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.core.task.task_discovery.frontend_url import (
    ConfigFrontendUrlProvider,
    FrontendUrlConfig,
    resolve_static_frontend_url,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.task_discovery_notify import (
    NotifyMessagesProvider,
)
from agentclaw.community.plugins.local.task_discovery_notify import (
    NullNotifyMessagesProvider,
)
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class CommunityTaskDiscoveryPortsModule(Module):
    """community: user_config 驱动的前端 URL + Null 通知窄端口。"""

    @singleton
    @provider
    def frontend_url_config(self) -> FrontendUrlConfig:
        """Read the ``task_discovery`` user_config block (community neutral)."""
        from agentclaw.community.di.modules.config_module import _block

        block = _block("task_discovery") or {}
        return FrontendUrlConfig(
            url=block.get("frontend_url", "") or "",
            url_pre=block.get("frontend_url_pre", "") or "",
            url_prod=block.get("frontend_url_prod", "") or "",
        )

    @singleton
    @provider
    @inject
    def frontend_url_provider(self, cfg: FrontendUrlConfig) -> ConfigFrontendUrlProvider:
        """Bind the provider with the env-resolved static value (ctor-pinned)."""
        env = get_current_env()
        static = resolve_static_frontend_url(cfg, env)
        logger.info(
            "ConfigFrontendUrlProvider: static frontend_url=%s (env=%s)",
            static or "<empty>", env,
        )
        return ConfigFrontendUrlProvider(static_url=static)

    @singleton
    @provider
    def notify_messages_provider(self) -> NotifyMessagesProvider:
        logger.info("NotifyMessagesProvider: NullNotifyMessagesProvider (community)")
        return NullNotifyMessagesProvider()


__all__ = ["CommunityTaskDiscoveryPortsModule"]