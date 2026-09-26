"""Service API Protocol for access-policy decisions and quota.

Re-export only. The Protocol is defined in its owning core module
(``core/access/policy_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.access.policy_service_protocol import (
    PolicyServiceProtocol,
)

__all__ = [
    "PolicyServiceProtocol",
]
