"""DI bindings for work orders and recipient notifications."""

from typing import Annotated

from injector import Binder, Module, inject, provider, singleton

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
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderDecisionCallbackDispatcher,
)
from agentclaw.community.core.work_orders.protocols import WorkOrderEventServiceProtocol
from agentclaw.community.core.work_orders.protocols import (
    SkillCollaboratorApprovalHandlerProtocol,
)
from agentclaw.community.core.skill_center.services.skill_collaborator_approval_handler import (
    SkillCollaboratorApprovalHandler,
)
from agentclaw.community.core.work_orders.services import (
    WorkOrderNotificationService,
    WorkOrderService,
)
from agentclaw.community.plugin_api.http_client import QUALIFIER_BCN, HttpClient
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    resign_principal_for_bcn,
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

    @singleton
    @provider
    @inject
    def work_order_decision_callbacks(
        self, http_client: Annotated[HttpClient, QUALIFIER_BCN]
    ) -> WorkOrderDecisionCallbackDispatcher:
        """Assemble the BCN decision callbacks with the principal re-signer.

        The callback calls BCN as the approving caller, which means re-addressing
        the gateway-signed principal the backend verified to the audience BCN
        requires. Which key and which audience that takes is deployment
        configuration, so it is bound here rather than read from the core seam
        that uses it.
        """
        return WorkOrderDecisionCallbackDispatcher(
            http_client, resign_principal=resign_principal_for_bcn
        )

    @singleton
    @provider
    @inject
    def skill_collaborator_approval_handler(
        self, repository: WorkOrderRepositoryProtocol
    ) -> SkillCollaboratorApprovalHandlerProtocol:
        """Assemble approval policy with environment at the DI boundary."""
        return SkillCollaboratorApprovalHandler(repository, get_current_env)
