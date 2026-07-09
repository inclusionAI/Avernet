"""Capability Protocol for notification dispatch.

Satisfies Rule 14 (Plugin Protocol in plugin_api), Rule 20 (local+prod),
Rule 21 (Noop+Mock).

Phase-1 channels: ``markdown`` and ``tc_card`` (DingTalk).
Future channels: email, SMS, webhook, etc.

Implementations:
  - ``CommunityNotifySender`` (community) — no channel, every send returns ``None``
  - ``NoopNotifySender`` (local/noop) — test double, every send returns ``None``
  - ``DingTalkNotifySender`` (prod) — routes to DingTalk batchSend
    or createAndDeliver based on channel selection

All methods must **never raise** — errors are caught internally
and logged, returning ``None`` instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@dataclass(frozen=True)
class NotifyMessage:
    """Channel-agnostic notification payload.

    ``extra`` carries channel-specific key-value pairs that the sender
    may or may not use — unrecognised keys are silently ignored.

    Governance-specific example::

        NotifyMessage(
            title="治理工单通知",
            body="# Bot XYZ 触发治理规则\\n...",
            recipient="staff123",
            deep_link="https://detail.page/notify-456",
            extra={
                "out_track_id_prefix": "gov-notify",
                "card_template_id": "bc2d6541-...",
                "card_id": "${AIX_CARD_ID}",
                "notification_data": {...},
            },
        )
    """

    title: str
    body: str
    recipient: str
    deep_link: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NotifySenderPlugin(Plugin, Protocol):
    """Send notifications through configurable external channels.

    Phase-1 channels: ``markdown`` and ``tc_card`` (DingTalk).
    Future channels: email, SMS, webhook, etc.

    Implementations:
      - ``CommunityNotifySender`` (community) — no channel available
      - ``NoopNotifySender`` (local/noop) — every send returns ``None``
      - ``DingTalkNotifySender`` (prod) — routes to DingTalk batchSend
        or createAndDeliver based on channel selection

    All methods must **never raise** — errors are caught internally
    and logged, returning ``None`` instead.
    """

    @property
    def channels(self) -> frozenset[str]:
        """Channels this sender supports (e.g. {"markdown", "tc_card"})."""
        ...

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        """Send a notification.

        Args:
            message: Channel-agnostic notification payload.
            channel: Desired channel (must be in ``self.channels``).
                If the sender does not support the requested channel,
                it falls back to its preferred channel and logs a warning.

        Returns:
            External message ID on success, ``None`` on failure.
        """
        ...