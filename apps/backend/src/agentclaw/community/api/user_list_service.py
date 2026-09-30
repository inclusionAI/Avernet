"""Neutral API contract for frontend user-list eligibility checks.

Re-export only. The Protocol is defined in its owning core module
(``core/user_list/user_list_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.user_list.user_list_service_protocol import (
    UserListServiceProtocol,
)

__all__ = [
    "UserListServiceProtocol",
]
