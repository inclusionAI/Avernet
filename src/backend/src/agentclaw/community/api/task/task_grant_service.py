"""任务认领 Bot 授权服务契约 + 实现(stateless secbaas 透传中继)。

Re-export only. The Protocol is defined in its owning core module
(``core/task/task_grant_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_grant_service_protocol import (
    GRANTED,
    GrantResult,
    OpenApiBotPort,
    REVOKED,
    RevokeResult,
    TaskClaimGrantServiceProtocol,
)

__all__ = [
    "GRANTED",
    "GrantResult",
    "OpenApiBotPort",
    "REVOKED",
    "RevokeResult",
    "TaskClaimGrantServiceProtocol",
]
