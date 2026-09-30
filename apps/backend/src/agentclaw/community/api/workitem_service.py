"""Service API Protocol for hosted-workspace work-item operations.

Re-export only. The Protocol is defined in its owning core module
(``core/aicoding/workitem_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.aicoding.workitem_service_protocol import (
    WorkItemServiceProtocol,
)

__all__ = [
    "WorkItemServiceProtocol",
]
