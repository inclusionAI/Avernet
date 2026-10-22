"""BBS-owned DingTalk delivery for digest confirmation cards.

The BBS provider reuses :class:`DingTalkNotifySender` with an instance-owned
``DingTalkSenderConfig``. It does not read task-discovery holders or environment
names. The AK secret is resolved lazily through ``SecretResolver``; an absent
configuration disables the card and returns ``None``.
"""

from __future__ import annotations

from agentclaw.community.di.bbs_digest_config import BbsDingTalkConfig
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bbs_digest_notify import (
    BbsDigestNotifyProvider,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.notify_sender import NotifyMessage
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugins.community.notify_sender import (
    CommunityNotifySender,
    DingTalkNotifySender,
    DingTalkSenderConfig,
)

logger = get_logger()


def _resolve_secret(
    secret_resolver: SecretResolver,
    secret_name: str,
) -> str:
    """Resolve one named BBS secret without ever exposing it in a config object."""

    try:
        secret = secret_resolver.get_secret(secret_name)
    except Exception as exc:  # noqa: BLE001 - the Plugin port never raises.
        logger.warning(
            "[bbs-digest-notify] DingTalk AK secret lookup failed: %s",
            exc,
        )
        return ""

    value = getattr(secret, "secret_value", None)
    if value is None and isinstance(secret, str):
        value = secret
    return str(value).strip() if value is not None else ""


@plugin_impl(
    mode=Mode.PROD,
    rationale="BBS-owned DingTalk interactive-card delivery",
)
class BbsDingTalkNotifyProvider(BbsDigestNotifyProvider):
    """Depend on the BBS DingTalk application, never the discovery account."""

    def __init__(
        self,
        config: BbsDingTalkConfig,
        secret_resolver: SecretResolver,
    ) -> None:
        self._sender = DingTalkNotifySender(
            CommunityNotifySender(),
            config=DingTalkSenderConfig(
                app_key=config.ak_id,
                read_app_secret=lambda: _resolve_secret(
                    secret_resolver,
                    config.ak_secret_name,
                ),
                robot_code=config.robot_code,
                card_template_id=config.card_template_id,
            ),
            strict_card=True,
        )

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "tc_card",
    ) -> str | None:
        return self._sender.send(message, channel=channel)


__all__ = ["BbsDingTalkNotifyProvider"]
