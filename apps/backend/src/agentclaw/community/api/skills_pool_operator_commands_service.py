"""Service API Protocol for operator-triggered Skills Pool commands.

Re-export only. The Protocol is defined in its owning core module
(``core/skills_pool/skills_pool_operator_commands_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.skills_pool.skills_pool_operator_commands_service_protocol import (
    BotSkillLayoutScope,
    OperatorCommandResult,
    SkillsPoolOperatorCommandsServiceProtocol,
)

__all__ = [
    "BotSkillLayoutScope",
    "OperatorCommandResult",
    "SkillsPoolOperatorCommandsServiceProtocol",
]
