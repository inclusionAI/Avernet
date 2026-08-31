"""Service API Protocol for the harness patch planner.

Re-export only. The Protocol is defined in its owning core module
(``core/harness/patch_planner_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.harness.patch_planner_service_protocol import (
    PatchPlannerProtocol,
)

__all__ = [
    "PatchPlannerProtocol",
]
