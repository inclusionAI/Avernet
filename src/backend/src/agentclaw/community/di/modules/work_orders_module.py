"""DI bindings for work orders and recipient notifications."""

from injector import Binder, Module, singleton

from agentclaw.community.api.work_order_service import (
    WorkOrderNotificationServiceProtocol,
    WorkOrderServiceProtocol,
)
from agentclaw.community.core.repository.implementations.work_orders import (
    WorkOrderRepository,
)
from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.work_orders.protocols import WorkOrderEventServiceProtocol
from agentclaw.community.core.work_orders.services import (
    WorkOrderNotificationService,
    WorkOrderService,
)


class WorkOrdersModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            WorkOrderRepositoryProtocol, to=WorkOrderRepository, scope=singleton
        )
        binder.bind(WorkOrderServiceProtocol, to=WorkOrderService, scope=singleton)
        binder.bind(WorkOrderEventServiceProtocol, to=WorkOrderService, scope=singleton)
        binder.bind(
            WorkOrderNotificationServiceProtocol,
            to=WorkOrderNotificationService,
            scope=singleton,
        )
