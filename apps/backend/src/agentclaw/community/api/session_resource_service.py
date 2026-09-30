"""Service API for session-scoped uploaded resources.

Re-export only. The Protocol is defined in its owning core module
(``core/session_resources/session_resource_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.session_resources.session_resource_service_protocol import (
    SessionResourceServiceProtocol,
)

__all__ = [
    "SessionResourceServiceProtocol",
]
