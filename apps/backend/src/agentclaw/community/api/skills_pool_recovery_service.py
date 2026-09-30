"""Service API Protocol for operator-directed Skills Pool repair.

Re-export only. The Protocol is defined in its owning core module
(``core/skills_pool/skills_pool_recovery_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skills_pool.skills_pool_recovery_service_protocol import (
    BotSkillLayoutScope,
    ManualRepairResolution,
    SkillsPoolRecoveryResult,
    SkillsPoolRecoveryServiceProtocol,
)

__all__ = [
    "BotSkillLayoutScope",
    "ManualRepairResolution",
    "SkillsPoolRecoveryResult",
    "SkillsPoolRecoveryServiceProtocol",
]
