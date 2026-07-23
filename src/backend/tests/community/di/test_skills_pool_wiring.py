"""Skills Pool 控制面业务边界的 DI 装配测试。"""

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
    SKILLS_POOL_RECONCILE_TASK,
    SkillsPoolReconcileTaskHandler,
    SkillsPoolReconcileWakeupListener,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.skills_pool.ports import (
    SkillsPoolRuntimeProtocol,
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.plugins.skills_pool_runtime import OpenClawSkillsPoolRuntime
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugins.skills_pool_layout_repository import (
    SkillsPoolLayoutRepository,
)
from agentclaw.community.plugins.skill_repository import SkillRepository


def test_skills_pool_control_plane_bindings_resolve() -> None:
    injector = build_injector(profile=DeployProfile.TEST)

    assert isinstance(
        injector.get(SkillsPoolLayoutRepositoryProtocol),
        SkillsPoolLayoutRepository,
    )
    assert isinstance(
        injector.get(SkillsPoolRolloutGate),
        SkillsPoolRolloutGate,
    )
    assert isinstance(
        injector.get(SkillsPoolMigrationClaimService),
        SkillsPoolMigrationClaimService,
    )
    assert isinstance(
        injector.get(SkillsPoolSkillRepositoryProtocol),
        SkillRepository,
    )
    assert isinstance(
        injector.get(SkillsPoolRuntimeProtocol),
        OpenClawSkillsPoolRuntime,
    )
    assert isinstance(
        injector.get(SkillsPoolReconcileService),
        SkillsPoolReconcileService,
    )
    assert isinstance(
        injector.get(SkillsPoolReconcileTaskHandler),
        SkillsPoolReconcileTaskHandler,
    )
    assert isinstance(
        injector.get(SkillsPoolReconcileWakeupListener),
        SkillsPoolReconcileWakeupListener,
    )


def test_skills_pool_reconcile_handler_registers_during_bootstrap() -> None:
    import asyncio

    injector = build_injector(profile=DeployProfile.TEST)
    listener = injector.get(SkillsPoolReconcileWakeupListener)

    asyncio.run(listener.bootstrap())

    assert isinstance(
        injector.get(HandlerRegistry).get(SKILLS_POOL_RECONCILE_TASK),
        SkillsPoolReconcileTaskHandler,
    )
