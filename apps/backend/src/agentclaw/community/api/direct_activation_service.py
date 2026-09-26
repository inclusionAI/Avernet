"""Service API for direct (Set-free) capability activation on one Bot.

Re-export only. The Protocol is defined in its owning core module
(``core/skill_center/direct_activation_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)

__all__ = [
    "DirectActivationServiceProtocol",
]
