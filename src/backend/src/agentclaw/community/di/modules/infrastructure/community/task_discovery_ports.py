"""Task-discovery ports — community binding.

Capability: task_discovery's narrow frontend-URL / notify-message plugin ports
(B-side of the plugin-ication refactor). Community ships no DingTalk channel
and no env-aware YAML frontend block, so both ports bind the local Null impls
(registered ``@plugin_impl(mode=LOCAL)``); the corp column overrides both with
``CorpFrontendUrlProvider`` / ``CorpNotifyMessagesProvider`` (plugins/prod).
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


class CommunityTaskDiscoveryPortsModule(Module):
    """community: Null impls for the task_discovery narrow ports."""

    @singleton
    @provider
    def frontend_url_provider(self) -> FrontendUrlProvider:
        logger.info("FrontendUrlProvider: NullFrontendUrlProvider (community)")
        return NullFrontendUrlProvider()

    @singleton
    @provider
    def notify_messages_provider(self) -> NotifyMessagesProvider:
        logger.info("NotifyMessagesProvider: NullNotifyMessagesProvider (community)")
        return NullNotifyMessagesProvider()


__all__ = ["CommunityTaskDiscoveryPortsModule"]