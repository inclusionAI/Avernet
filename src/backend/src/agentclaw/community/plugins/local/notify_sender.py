"""NoopNotifySender — local/noop notification sender (Rule 21).

Test/offline double: every send returns ``None``.
Inherits MockSeam for testability (``set_override`` / ``calls`` API).
Bound by testing_* DI modules, NOT by CommunityNotifyModule.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

log = get_logger(__name__)


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="Community ships no messaging channel",
)
class NoopNotifySender(MockSeam, NotifySenderPlugin):
    """Every send returns ``None`` — local/test dev has no external channel."""

    @property
    def channels(self) -> frozenset[str]:
        return frozenset()

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        log.debug("[NoopNotifySender] send(channel=%s) → no-op", channel)
        return None