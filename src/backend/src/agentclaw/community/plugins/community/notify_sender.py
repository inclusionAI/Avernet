"""Community ``NotifySenderPlugin`` — log-only notification sender.

A real, deployable impl (not a MockSeam test double). The community build
ships no DingTalk / Slack / Email channel — notifications are delivered to
the standard logger via ``get_logger``. Since writing to the log IS the
delivery, ``send()`` returns a log-based message ID (not ``None``).

Corp deployments bind ``DingTalkNotifySender`` instead for real delivery.

Mirrors ``CommunityDRMReader`` (degenerate but real, every flag is unset)
and ``NoApprovalWorkflow`` (unavailable, tells callers so). Not a
``MockSeam`` — bound directly by ``CommunityNotifyModule``.
"""
from __future__ import annotations

import uuid

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

log = get_logger(__name__)


class CommunityNotifySender(NotifySenderPlugin):
    """Community profile: log-only notification sender.

    ``send()`` writes the notification to the standard logger and returns
    a ``log-<uuid>`` message ID — writing to the log IS the delivery,
    so the caller treats this as a successful send.
    """

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"log"})

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        msg_id = f"log-{uuid.uuid4().hex[:12]}"
        log.info(
            "[CommunityNotifySender] send(channel=%s → log, recipient=%s, "
            "title=%r, deep_link=%s) → %s",
            channel,
            message.recipient,
            message.title[:80],
            message.deep_link[:60] if message.deep_link else "",
            msg_id,
        )
        log.debug(
            "[CommunityNotifySender] message body:\n%s",
            message.body[:500] if message.body else "",
        )
        return msg_id