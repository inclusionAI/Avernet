"""Service API Protocol for beta invite quota.

Re-export only. The Protocol is defined in its owning core module
(``core/common_config/beta_quota_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.common_config.beta_quota_service_protocol import (
    BetaQuotaServiceProtocol,
)

__all__ = [
    "BetaQuotaServiceProtocol",
]
