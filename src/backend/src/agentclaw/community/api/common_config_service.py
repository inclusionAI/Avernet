"""Service API Protocol for common configuration.

Re-export only. The Protocol is defined in its owning core module
(``core/common_config/common_config_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.common_config.common_config_service_protocol import (
    CommonConfigServiceProtocol,
)

__all__ = [
    "CommonConfigServiceProtocol",
]
