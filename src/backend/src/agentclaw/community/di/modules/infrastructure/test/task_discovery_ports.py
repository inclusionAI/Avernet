"""Task-discovery ports — test / singlebox binding.

- ``ConfigFrontendUrlProvider``: 前端 URL 配置端口（非 plugin），test/singlebox
  默认空值（下游回落构造默认 localhost），保证契约/DI 消费路径可解析。
- ``NotifyMessagesProvider``: task_discovery 通知窄端口（plugin 契约），test
  绑 Null 实现。
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.core.task.task_discovery.frontend_url import (
    ConfigFrontendUrlProvider,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.task_discovery_notify import (
    NotifyMessagesProvider,
)
from agentclaw.community.plugins.local.task_discovery_notify import (
    NullNotifyMessagesProvider,
)


logger = get_logger()


class TestFrontendUrlProviderModule(Module):
    """test / singlebox: empty static frontend URL provider."""

    @singleton
    @provider
    def frontend_url_provider(self) -> ConfigFrontendUrlProvider:
        logger.info("ConfigFrontendUrlProvider: empty static (test)")
        return ConfigFrontendUrlProvider()


class TestNotifyMessagesProviderModule(Module):
    """test / singlebox: Null notify provider (send → None)."""

    @singleton
    @provider
    def notify_messages_provider(self) -> NotifyMessagesProvider:
        logger.info("NotifyMessagesProvider: NullNotifyMessagesProvider (test)")
        return NullNotifyMessagesProvider()


__all__ = [
    "TestFrontendUrlProviderModule",
    "TestNotifyMessagesProviderModule",
]