"""Notify-sender concern — community binding.

Binds ``CommunityNotifySender`` (log-only, no real delivery) for the
community/singlebox profile. Corp deployments bind ``DingTalkNotifySender``
via ``CorpNotifyModule`` instead.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

logger = get_logger(__name__)


class CommunityNotifyModule(Module):
    """community: log-only sender (no external channel)."""

    @singleton
    @provider
    def _notify_sender(self) -> NotifySenderPlugin:
        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender,
        )

        logger.info(
            "[community.notify] Binding CommunityNotifySender "
            "(log-only; no real delivery)",
        )
        return CommunityNotifySender()