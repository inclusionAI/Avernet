"""Commission composition slice, installed by TaskModule.

Keep versioned admission, shared G1 enforcement and outbound delivery together;
repository, catalog and L5 services remain shared TaskModule dependencies.
"""

from injector import Binder, Module, singleton

from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
    MockA2aDelivery,
)
from agentclaw.community.core.task.context_collab.products.task_commission.dispatch import (
    A2aTaskDispatch,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskCommissionServiceProtocol,
    TaskDispatchPort,
    TaskMethodOpsPort,
)
from agentclaw.community.di.modules.task_commission_authorization import (
    CommissionAdmissionAuthorizer,
)
from agentclaw.community.di.modules.task_commission_self_application import (
    TaskCommissionSelfApplicationAdapter,
)


class TaskCommissionModule(Module):
    def configure(self, binder: Binder) -> None:
        # Self-application profiles require scoped Open admission; @2 additionally
        # requires Single selection. Generic L4 and product Work share this guard.
        binder.bind(
            ContextCollabAuthorizerProtocol,
            to=CommissionAdmissionAuthorizer,
            scope=singleton,
        )
        binder.bind(
            TaskCommissionServiceProtocol,
            to=TaskCommissionService,
            scope=singleton,
        )
        binder.bind(
            TaskMethodOpsPort,
            to=TaskCommissionSelfApplicationAdapter,
            scope=singleton,
        )
        # Local outbound delivery is still mock, not live Bot execution.
        binder.bind(A2aDeliveryPort, to=MockA2aDelivery, scope=singleton)
        binder.bind(TaskDispatchPort, to=A2aTaskDispatch, scope=singleton)
