"""DingTalkNotifySender — unified DingTalk notification sender (community).

Unified DingTalk notification sender that maps the generic
``NotifySenderPlugin`` contract onto the DingTalk-specific
leaf components (``DingTalkMarkdownSender`` / ``DingTalkTcCardSender``
in ``dingtalk_sender.py``, unchanged).

This is the **adapter** layer; the leaf components are the **method objects**
that own the actual HTTP transport.  Separating the two keeps the
DingTalk API details (token lifecycle, endpoint paths, payload shapes)
in one place while the adapter only concerns itself with protocol mapping.

Migrated from ``agentclaw.corp.plugins.prod.dingtalk_notify_sender`` so the
community/singlebox profile can dispatch real DingTalk notifications without
importing the corp package (B11 corp-free boundary). The corp package
re-exports ``DingTalkNotifySender`` from its historical path so existing corp
call sites are unchanged.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

log = get_logger(__name__)

_SUPPORTED_CHANNELS = frozenset({"markdown", "tc_card"})


class DingTalkNotifySender(NotifySenderPlugin):
    """Unified DingTalk notification sender — routes to markdown or TC card.

    Internally composes ``DingTalkMarkdownSender`` and ``DingTalkTcCardSender``
    (the existing leaf components in ``dingtalk_sender.py``, unchanged).
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        robot_code: str,
        card_template_id: str = "",
    ) -> None:
        from agentclaw.community.plugins.community.dingtalk_sender import (
            DingTalkMarkdownSender,
            DingTalkSenderConfig,
            DingTalkTcCardSender,
        )

        config = DingTalkSenderConfig(
            app_key=app_key,
            app_secret=app_secret,
            robot_code=robot_code,
        )
        self._markdown = DingTalkMarkdownSender(config=config)
        self._tc_card = DingTalkTcCardSender(
            config=config,
            card_template_id=card_template_id,
        )

    @property
    def channels(self) -> frozenset[str]:
        return _SUPPORTED_CHANNELS

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        effective_channel = channel if channel in _SUPPORTED_CHANNELS else "markdown"
        if effective_channel != channel:
            log.warning(
                "[DingTalkNotifySender] Unsupported channel=%s, falling back to %s",
                channel,
                effective_channel,
            )

        if effective_channel == "tc_card":
            return self._send_tc_card(message)
        return self._send_markdown(message)

    def _send_markdown(self, msg: NotifyMessage) -> str | None:
        return self._markdown.send_markdown(
            user_id=msg.recipient,
            title=msg.title,
            content=msg.body,
        )

    def _send_tc_card(self, msg: NotifyMessage) -> str | None:
        return self._tc_card.send_tc_card(
            user_id=msg.recipient,
            reason=msg.body,
            detail_link=msg.deep_link,
            bot_id=msg.extra.get("bot_id", ""),
            card_id=msg.extra.get("card_id", ""),
            notification_data=msg.extra.get("notification_data", {}),
            out_track_id_prefix=msg.extra.get("out_track_id_prefix", "dingtalk"),
        )