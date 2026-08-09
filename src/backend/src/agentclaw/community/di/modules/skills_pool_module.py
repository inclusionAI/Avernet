"""Skills Pool 控制面状态、灰度门禁与认领服务装配。"""

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.skills_pool_operational_query_service import (
    SkillsPoolOperationalQueryServiceProtocol,
)
from agentclaw.community.api.skills_pool_operator_commands_service import (
    SkillsPoolOperatorCommandsServiceProtocol,
)
from agentclaw.community.api.skills_pool_recovery_service import (
    SkillsPoolRecoveryServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollback_service import (
    SkillsPoolRollbackServiceProtocol,
)
from agentclaw.community.api.skills_pool_rollout_service import (
    SkillsPoolRolloutServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.skills_pool.claim_service import (
    SkillsPoolMigrationClaimService,
)
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard
from agentclaw.community.core.skills_pool.operational_query import (
    SkillsPoolOperationalQuery,
)
from agentclaw.community.core.skills_pool.operations import (
    SkillsPoolRolloutOperations,
)
from agentclaw.community.core.skills_pool.operator_commands import (
    SkillsPoolOperatorCommands,
)
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
from agentclaw.community.core.skills_pool.rollout_gate import (
    SkillsPoolRolloutGate,
)
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolRolloutRepositoryProtocol
from agentclaw.community.core.skills_pool.reconcile_service import (
    SkillsPoolReconcileService,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SkillsPoolReconcileTaskHandler,
    SkillsPoolReconcileWakeupListener,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol
from agentclaw.community.core.skills_pool.quarantine import SkillsPoolQuarantineCleanupTaskHandler, SkillsPoolQuarantineService
from agentclaw.community.core.repository.protocols.skills_pool import QuarantineRepositoryProtocol
from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRecoveryService,
    SkillsPoolRollbackService,
)
from agentclaw.community.plugins.skill_repository import SkillRepository
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.plugins.skills_pool_runtime import SkillsPoolRuntime
from agentclaw.community.plugins.skills_pool_layout_repository import (
    SkillsPoolLayoutRepository,
)
from agentclaw.community.plugins.skills_pool_rollout_repository import (
    SkillsPoolRolloutRepository,
)


class SkillsPoolModule(Module):
    """绑定与部署 profile 无关的统一 ORM 仓储和领域服务。"""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            SkillsPoolLayoutRepositoryProtocol,
            to=SkillsPoolLayoutRepository,
            scope=singleton,
        )
        binder.bind(
            QuarantineRepositoryProtocol,
            to=SkillsPoolLayoutRepository,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRolloutGate,
            to=SkillsPoolRolloutGate,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolMigrationClaimService,
            to=SkillsPoolMigrationClaimService,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolSkillRepositoryProtocol,
            to=SkillRepository,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRuntimeProtocol,
            to=SkillsPoolRuntime,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolReconcileService,
            to=SkillsPoolReconcileService,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolEditGuard,
            to=SkillsPoolEditGuard,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRolloutOperations,
            to=SkillsPoolRolloutOperations,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRolloutRepositoryProtocol,
            to=SkillsPoolRolloutRepository,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolOperationalQuery,
            to=SkillsPoolOperationalQuery,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolOperatorCommands,
            to=SkillsPoolOperatorCommands,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRecoveryService,
            to=SkillsPoolRecoveryService,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRollbackService,
            to=SkillsPoolRollbackService,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def rollout_service_protocol(
        self,
        service: SkillsPoolRolloutOperations,
    ) -> SkillsPoolRolloutServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def operational_query_service_protocol(
        self,
        service: SkillsPoolOperationalQuery,
    ) -> SkillsPoolOperationalQueryServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def operator_commands_service_protocol(
        self,
        service: SkillsPoolOperatorCommands,
    ) -> SkillsPoolOperatorCommandsServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def recovery_service_protocol(
        self,
        service: SkillsPoolRecoveryService,
    ) -> SkillsPoolRecoveryServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def rollback_service_protocol(
        self,
        service: SkillsPoolRollbackService,
    ) -> SkillsPoolRollbackServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def reconcile_task_handler(
        self,
        claim_service: SkillsPoolMigrationClaimService,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        reconcile_service: SkillsPoolReconcileService,
        quarantine_repository: QuarantineRepositoryProtocol,
        task_queue_service: TaskQueueService,
    ) -> SkillsPoolReconcileTaskHandler:
        return SkillsPoolReconcileTaskHandler(
            claim_service=claim_service,
            layout_repository=layout_repository,
            reconcile_service=reconcile_service,
            quarantine_repository=quarantine_repository,
            task_queue_service=task_queue_service,
        )

    @singleton
    @provider
    @inject
    def reconcile_wakeup_listener(
        self,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        task_queue_service: TaskQueueService,
        registry: HandlerRegistry,
        task_handler: SkillsPoolReconcileTaskHandler,
        quarantine_task_handler: SkillsPoolQuarantineCleanupTaskHandler,
    ) -> SkillsPoolReconcileWakeupListener:
        return SkillsPoolReconcileWakeupListener(
            binding_repository=binding_repository,
            bot_repository=bot_repository,
            task_queue_service=task_queue_service,
            registry=registry,
            task_handler=task_handler,
            quarantine_task_handler=quarantine_task_handler,
        )

    @singleton
    @provider
    @inject
    def quarantine_service(
        self,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        quarantine_repository: QuarantineRepositoryProtocol,
        runtime: SkillsPoolRuntimeProtocol,
    ) -> SkillsPoolQuarantineService:
        return SkillsPoolQuarantineService(
            quarantine_repository=quarantine_repository,
            layout_repository=layout_repository,
            runtime=runtime,
        )

    @singleton
    @provider
    @inject
    def quarantine_task_handler(
        self,
        service: SkillsPoolQuarantineService,
    ) -> SkillsPoolQuarantineCleanupTaskHandler:
        return SkillsPoolQuarantineCleanupTaskHandler(service)
