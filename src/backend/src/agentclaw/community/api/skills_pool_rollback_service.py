"""Service API Protocol for explicit Skills Pool rollback.

Re-export only. The Protocol is defined in its owning core module
(``core/skills_pool/skills_pool_rollback_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skills_pool.skills_pool_rollback_service_protocol import (
    BotSkillLayoutScope,
    SkillsPoolRollbackOutcome,
    SkillsPoolRollbackResult,
    SkillsPoolRollbackServiceProtocol,
)

__all__ = [
    "BotSkillLayoutScope",
    "SkillsPoolRollbackOutcome",
    "SkillsPoolRollbackResult",
    "SkillsPoolRollbackServiceProtocol",
]
