"""Service API Protocol for channel CRUD + OpenClaw sync.

Re-export only. The Protocol is defined in its owning core module
(``core/channel/channel_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.channel.channel_service_protocol import (
    ChannelRecord,
    ChannelServiceProtocol,
)

__all__ = [
    "ChannelRecord",
    "ChannelServiceProtocol",
]
