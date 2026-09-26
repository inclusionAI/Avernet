"""Service API Protocol for quality task management.

Re-export only. The Protocol is defined in its owning core module
(``core/quality/quality_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.quality.quality_service_protocol import (
    QualityTaskRecord,
    QualityTaskServiceProtocol,
)

__all__ = [
    "QualityTaskRecord",
    "QualityTaskServiceProtocol",
]
