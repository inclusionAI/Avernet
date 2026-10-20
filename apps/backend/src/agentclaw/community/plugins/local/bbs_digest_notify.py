"""Local no-op implementation for the BBS digest notification port."""

from __future__ import annotations

from agentclaw.community.plugin_api.bbs_digest_notify import (
    BbsDigestNotifyProvider,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.NOOP,
    rationale="offline tests cannot contact the BBS-owned DingTalk application",
)
class NullBbsDigestProvider(MockSeam, BbsDigestNotifyProvider):
    """Return ``None`` for every message; no test fixture is needed."""

    def send(self, message, *, channel: str = "tc_card") -> str | None:
        return None


__all__ = ["NullBbsDigestProvider"]
