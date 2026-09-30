"""Service API Protocol for task processor.

Re-export only. The Protocol is defined in its owning core module
(``core/quality/task_processor_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.quality.task_processor_service_protocol import (
    QualityTaskRecord,
    TaskProcessorProtocol,
)

__all__ = [
    "QualityTaskRecord",
    "TaskProcessorProtocol",
]
