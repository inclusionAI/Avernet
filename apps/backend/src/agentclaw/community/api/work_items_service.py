"""Service API Protocol for user-level Agent Coding work-items.

Re-export only. The Protocol is defined in its owning core module
(``core/aicoding/work_items_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing it from
here.
"""

from __future__ import annotations

from agentclaw.community.core.aicoding.work_items_service_protocol import (
    AICodingWorkItemsServiceProtocol,
)

__all__ = [
    "AICodingWorkItemsServiceProtocol",
]
