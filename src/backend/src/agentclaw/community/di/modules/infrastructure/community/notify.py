"""Notify-sender concern — community binding.

Binds ``CommunityNotifySender`` (log-only, no real delivery) for the
community/singlebox profile. 当钉钉凭证就绪（``TASK_DISCOVERY_DINGTALK_*`` /
回退 ``SINGLEBOX_DINGTALK_*`` + ``TASK_DISCOVERY_CARD_TEMPLATE_ID``）时，
绑定 ``DingTalkNotifySender``（装饰 ``CommunityNotifySender``：先日志再投递
钉钉交互卡片，两者同时进行）。Corp deployments bind ``DingTalkNotifySender``
via ``CorpNotifyModule`` instead.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

logger = get_logger(__name__)


class CommunityNotifyModule(Module):
    """community: log-only sender, or DingTalk-wrapped when creds are configured."""

    @singleton
    @provider
    def _notify_sender(self) -> NotifySenderPlugin:
        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender,
            DingTalkNotifySender,
        )

        inner = CommunityNotifySender()
        if DingTalkNotifySender._configured():
            logger.info(
                "[community.notify] Binding DingTalkNotifySender(CommunityNotifySender) "
                "(log + dingtalk interactive card)",
            )
            return DingTalkNotifySender(inner)
        logger.info(
            "[community.notify] Binding CommunityNotifySender "
            "(log-only; no real delivery)",
        )
        return inner