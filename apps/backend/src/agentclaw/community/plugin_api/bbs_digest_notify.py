"""Narrow Plugin Protocol for BBS digest owner notifications.

BBS digest has its own DingTalk application and card template. It must not
reuse the task-discovery notification binding or holders, because a change to
one application would silently change the sending identity of the other.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin
from agentclaw.community.plugin_api.notify_sender import NotifyMessage


@runtime_checkable
class BbsDigestNotifyProvider(Plugin, Protocol):
    """Send one BBS digest card through the BBS-owned notification account."""

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "tc_card",
    ) -> str | None:
        """Send the card and return its external message ID.

        Implementations MUST never raise. They return ``None`` when delivery is
        unavailable so digest state can record a notification-port failure
        without losing the already-persisted confirmation batch.
        """
        ...


class NullBbsDigestNotifyProvider:
    """Offline implementation: never raise and never deliver."""

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "tc_card",
    ) -> str | None:
        return None


__all__ = [
    "BbsDigestNotifyProvider",
    "NullBbsDigestNotifyProvider",
]
