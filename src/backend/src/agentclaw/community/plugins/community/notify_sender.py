"""Community ``NotifySenderPlugin`` — no external messaging channel.

A real, deployable impl (not a MockSeam test double). The community build
ships no DingTalk / Slack / Email channel, so every ``send`` returns ``None``
— the same graceful outcome the corp empty-credentials path already produces.

Callers already handle ``None``: governance marks the notify for retry,
audit records the failure. The community deployment simply has no channel
to retry *to*, so those records remain in ``pending`` / ``failed`` status
until a real channel is configured. This is intentional — not a bug.

Mirrors ``CommunityDRMReader`` (degenerate but real, every flag is unset)
and ``NoApprovalWorkflow`` (unavailable, tells callers so). Not a
``MockSeam`` — bound directly by ``CommunityNotifyModule``.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

log = get_logger(__name__)

_NO_CHANNEL = (
    "No notification channel configured in the community build; "
    "configure a DingTalk / Slack / Email sender to enable delivery"
)


class CommunityNotifySender(NotifySenderPlugin):
    """Community profile: no notification channel available.

    Every ``send`` returns ``None``. The calling service (governance scan,
    future notification consumers) already handles ``None`` as "send failed,
    will retry next tick" — this is a graceful degradation, not an error.

    Production governance records will stay in ``pending``/``failed``
    status until a real channel is bound (corp deployment swaps this
    module for ``DingTalkNotifySender``).
    """

    @property
    def channels(self) -> frozenset[str]:
        return frozenset()

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        log.info(
            "[CommunityNotifySender] send(channel=%s, recipient=%s, title=%r) "
            "→ no-op: %s",
            channel, message.recipient, message.title[:60], _NO_CHANNEL,
        )
        return None