"""Service API contracts for work orders and recipient notifications.

Re-export only. The Protocol is defined in its owning core module
(``core/work_orders/work_order_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.work_orders.work_order_service_protocol import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)

__all__ = [
    "WorkOrderNotificationServiceProtocol",
    "WorkOrderServiceProtocol",
]
