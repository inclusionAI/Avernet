"""Task-discovery ports — test / singlebox binding (Null impls).

Binds ``FrontendUrlProvider`` / ``NotifyMessagesProvider`` (the task_discovery
plugin ports declared in ``plugin_api.frontend_url`` /
``plugin_api.task_discovery_notify``) to the local Null impls, so contract
suites can resolve them via ``world.get(...)`` and the fail-closed consumer
fallback is exercised with a bound (rather than missing) binding.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.frontend_url import FrontendUrlProvider
from agentclaw.community.plugin_api.task_discovery_notify import (
    NotifyMessagesProvider,
)
from agentclaw.community.plugins.local.frontend_url import NullFrontendUrlProvider
from agentclaw.community.plugins.local.task_discovery_notify import (
    NullNotifyMessagesProvider,
)


logger = get_logger()


class TestFrontendUrlProviderModule(Module):
    """test / singlebox: Null frontend URL provider (empty string)."""

    @singleton
    @provider
    def frontend_url_provider(self) -> FrontendUrlProvider:
        logger.info("FrontendUrlProvider: NullFrontendUrlProvider (test)")
        return NullFrontendUrlProvider()


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