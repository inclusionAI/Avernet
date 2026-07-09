"""Notify-sender concern — community binding.

When DingTalk credentials are configured (``user_config.dingtalk.app_key``
non-empty), dispatches real DingTalk notifications via
``DingTalkNotifySender`` — so the community/singlebox profile can send real
governance notifications without importing the corp package (B11 corp-free
boundary). When credentials are empty, degrades to ``CommunityNotifySender``
(every send returns ``None``), preserving the historical no-op behaviour for
credential-less community deployments.

Channel selection mirrors ``CorpNotifyModule``:
  - ``app_key`` non-empty → ``DingTalkNotifySender`` (``tc_card`` channel uses
    ``tc_card_template_id``; missing template auto-degrades to Markdown at
    runtime inside the leaf).
  - ``app_key`` empty → ``CommunityNotifySender`` (no-op).
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.di.config import (
    EconomyGovernanceConfig,
    GovernanceDingTalkConfig,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import NotifySenderPlugin

logger = get_logger(__name__)


class CommunityNotifyModule(Module):
    """community: DingTalk sender when credentials present, else no-op."""

    @singleton
    @provider
    @inject
    def _notify_sender(
        self,
        dingtalk_config: GovernanceDingTalkConfig,
        gov_config: EconomyGovernanceConfig,
    ) -> NotifySenderPlugin:
        if dingtalk_config.app_key:
            from agentclaw.community.plugins.community.dingtalk_notify_sender import (
                DingTalkNotifySender,
            )

            template_id = gov_config.tc_card_template_id
            if template_id:
                logger.info(
                    "[community.notify] Using DingTalkNotifySender "
                    "with template_id=%s***",
                    template_id[:8] if len(template_id) >= 8 else template_id,
                )
            else:
                logger.warning(
                    "[community.notify] notify_channel='tc_card' but "
                    "tc_card_template_id not configured — DingTalkNotifySender "
                    "will auto-degrade to Markdown at runtime",
                )
            return DingTalkNotifySender(
                app_key=dingtalk_config.app_key,
                app_secret=dingtalk_config.app_secret,
                robot_code=dingtalk_config.robot_code,
                card_template_id=template_id,
            )

        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender,
        )

        logger.info(
            "[community.notify] No DingTalk credentials configured "
            "— binding CommunityNotifySender (no-op; send returns None)",
        )
        return CommunityNotifySender()