"""Skills Pool 控制面状态、灰度门禁与认领服务装配。"""

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
)
from agentclaw.community.core.skills_pool.claim_service import (
    SkillsPoolMigrationClaimService,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    SkillsPoolRolloutGate,
)
from agentclaw.community.core.skills_pool.reconcile_service import (
    SkillsPoolReconcileService,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SkillsPoolReconcileTaskHandler,
    SkillsPoolReconcileWakeupListener,
)
from agentclaw.community.core.skills_pool.ports import (
    SkillsPoolRuntimeProtocol,
    SkillsPoolSkillRepositoryProtocol,
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


class SkillsPoolModule(Module):
    """绑定与部署 profile 无关的统一 ORM 仓储和领域服务。"""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            SkillsPoolLayoutRepositoryProtocol,
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

    @singleton
    @provider
    @inject
    def reconcile_task_handler(
        self,
        claim_service: SkillsPoolMigrationClaimService,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        reconcile_service: SkillsPoolReconcileService,
    ) -> SkillsPoolReconcileTaskHandler:
        return SkillsPoolReconcileTaskHandler(
            claim_service=claim_service,
            layout_repository=layout_repository,
            reconcile_service=reconcile_service,
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
    ) -> SkillsPoolReconcileWakeupListener:
        return SkillsPoolReconcileWakeupListener(
            binding_repository=binding_repository,
            bot_repository=bot_repository,
            task_queue_service=task_queue_service,
            registry=registry,
            task_handler=task_handler,
        )
