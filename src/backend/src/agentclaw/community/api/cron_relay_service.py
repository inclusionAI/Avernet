"""Service API Protocol for cron relay (proxy + listing).

Re-export only. The Protocol is defined in its owning core module
(``core/cron/cron_relay_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.cron.cron_relay_service_protocol import (
    CronRelayServiceProtocol,
)

__all__ = [
    "CronRelayServiceProtocol",
]
