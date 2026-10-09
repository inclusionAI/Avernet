"""Service API Protocol for the aicoding data-proxy.

Re-export only. The Protocol is defined in its owning core module
(``core/aicoding/data_proxy_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.aicoding.data_proxy_service_protocol import (
    DataProxyServiceProtocol,
)

__all__ = [
    "DataProxyServiceProtocol",
]
