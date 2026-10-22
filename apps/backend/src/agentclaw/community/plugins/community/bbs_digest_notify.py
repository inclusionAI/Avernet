"""BBS-owned DingTalk delivery for digest confirmation cards.

This sender intentionally does not read the task-discovery holders or
environment names. The BBS robot and card template are captured from
``user_config.bbs.dingtalk``; only the AK secret is resolved lazily through
``SecretResolver``. An absent block disables the card and returns ``None``.
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
)

logger = get_logger()


class BbsDingTalkCardSender(DingTalkNotifySender):
    """Pin card delivery to the BBS account without discovery-holder fallbacks."""

    def __init__(
        self,
        inner: object,
        config: BbsDingTalkConfig,
        secret_resolver: SecretResolver,
    ) -> None:
        super().__init__(inner)  # type: ignore[arg-type]
        self._account = {
            "ak_id": config.ak_id,
            "robot_code": config.robot_code,
            "card_template_id": config.card_template_id,
        }
        self._secret_name = config.ak_secret_name
        self._secret_resolver = secret_resolver

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "tc_card",
    ) -> str | None:
        """Depend only on the BBS card result; never fall back to a log sender."""
        if channel != "tc_card":
            logger.warning(
                "[bbs-digest-notify] unsupported channel=%s; supported=tc_card",
                channel,
            )
            return None
        try:
            return self._send_dingtalk_card(message)
        except Exception as exc:  # noqa: BLE001 - the Plugin port never raises.
            logger.warning(
                "[bbs-digest-notify] DingTalk card dispatch failed: %s",
                exc,
            )
            return None

    def _recipient_account_id(self, message: NotifyMessage) -> str:
        """Use the digest owner directly; never read task-discovery overrides."""
        return message.recipient

    def _resolve_secret(self) -> str:
        try:
            secret = self._secret_resolver.get_secret(self._secret_name)
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

    def _resolve(self, key: str, _env_name: str, _env_fallback: str) -> str:
        if key == "ak_secret":
            return self._resolve_secret()
        return self._account.get(key, "")

    def _resolve_source(self, key: str) -> str:
        return "secret_resolver" if key == "ak_secret" else "bbs_config"

    def _configured(self) -> bool:
        return all(
            self._resolve(key, "", "")
            for key, _env_name, _env_fallback in self._CRED_KEYS
        )


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
        self._sender = BbsDingTalkCardSender(
            CommunityNotifySender(),
            config,
            secret_resolver,
        )

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "tc_card",
    ) -> str | None:
        if not self._sender._configured():
            logger.warning(
                "[bbs-digest-notify] BBS DingTalk card not configured; "
                "returning failure instead of treating a log line as delivery"
            )
            return None
        return self._sender.send(message, channel=channel)


__all__ = [
    "BbsDingTalkCardSender",
    "BbsDingTalkNotifyProvider",
]
