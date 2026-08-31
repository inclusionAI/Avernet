"""Service API Protocol for Skills Pool rollout control.

Re-export only. The Protocol is defined in its owning core module
(``core/skills_pool/skills_pool_rollout_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skills_pool.skills_pool_rollout_service_protocol import (
    BatchPromotionEvidence,
    RolloutConfigSnapshot,
    RolloutControlGroup,
    SkillsPoolRolloutServiceProtocol,
    WhitelistMutationResult,
)

__all__ = [
    "BatchPromotionEvidence",
    "RolloutConfigSnapshot",
    "RolloutControlGroup",
    "SkillsPoolRolloutServiceProtocol",
    "WhitelistMutationResult",
]
