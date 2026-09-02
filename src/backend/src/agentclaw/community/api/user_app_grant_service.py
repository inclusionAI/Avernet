"""Service API Protocol for user-granted account-level authorizations.

Re-export only. The Protocol is defined in its owning core module
(``core/user_app_grant/user_app_grant_service_protocol.py``) so the concrete
service can inherit it without a ``core -> api`` waiver; adapters keep
importing it from here.
"""

from __future__ import annotations

from agentclaw.community.core.user_app_grant.user_app_grant_service_protocol import (
    UserAppGrantRecord,
    UserAppGrantServiceProtocol,
)

__all__ = [
    "UserAppGrantRecord",
    "UserAppGrantServiceProtocol",
]
