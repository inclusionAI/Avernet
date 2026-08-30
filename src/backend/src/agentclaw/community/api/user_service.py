"""Service API Protocol for user (access-policy) CRUD.

Re-export only. The Protocol is defined in its owning core module
(``core/access/user_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.access.user_service_protocol import (
    UserInfoRecord,
    UserServiceProtocol,
)

__all__ = [
    "UserInfoRecord",
    "UserServiceProtocol",
]
