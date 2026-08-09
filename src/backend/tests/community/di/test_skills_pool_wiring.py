"""Skills Pool 控制面业务边界的 DI 装配测试。"""

from dataclasses import replace

import pytest

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
from agentclaw.community.core.skills_pool.claim_service import (
    SkillsPoolMigrationClaimService,
)
from agentclaw.community.core.skills_pool.operational_query import (
    SkillsPoolOperationalQuery,
)
from agentclaw.community.core.skills_pool.operations import (
    SkillsPoolRolloutOperations,
)
from agentclaw.community.core.skills_pool.operator_commands import (
    SkillsPoolOperatorCommands,
)
from agentclaw.community.core.skills_pool.recovery_service import (
    SkillsPoolRecoveryService,
    SkillsPoolRollbackService,
)
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
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
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
    SkillSymlinkListener,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol
from agentclaw.community.plugins.skills_pool_runtime import OpenClawSkillsPoolRuntime
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugins.skills_pool_layout_repository import (
    SkillsPoolLayoutRepository,
)
from agentclaw.community.plugins.skill_repository import SkillRepository


@pytest.mark.parametrize(
    ("protocol", "implementation"),
    [
        (SkillsPoolRolloutServiceProtocol, SkillsPoolRolloutOperations),
        (
            SkillsPoolOperationalQueryServiceProtocol,
            SkillsPoolOperationalQuery,
        ),
        (
            SkillsPoolOperatorCommandsServiceProtocol,
            SkillsPoolOperatorCommands,
        ),
        (SkillsPoolRecoveryServiceProtocol, SkillsPoolRecoveryService),
        (SkillsPoolRollbackServiceProtocol, SkillsPoolRollbackService),
    ],
)
def test_skills_pool_service_api_conformance_and_alias_identity(
    protocol: type[object],
    implementation: type[object],
) -> None:
    injector = build_injector(profile=DeployProfile.TEST)

    concrete = injector.get(implementation)

    assert isinstance(concrete, protocol)
    assert injector.get(protocol) is concrete


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


def test_desktop_pool_active_uses_the_public_layout_resolver(
    monkeypatch,
) -> None:
    injector = build_injector(profile=DeployProfile.TEST)
    bot_repository = injector.get(BotRepository)
    layout_repository = injector.get(SkillsPoolLayoutRepositoryProtocol)
    scope = BotSkillLayoutScope(
        env="pre",
        entity_id="staff_1",
        bot_id="desktop-1",
    )
    monkeypatch.setattr(
        bot_repository,
        "get_by_id_and_owner",
        lambda *_: {
            "bot_id": scope.bot_id,
            "entity_id": scope.entity_id,
            "env": scope.env,
            "bot_type": "desktop",
            "active_engine": "hermes",
        },
    )
    monkeypatch.setattr(
        layout_repository,
        "get",
        lambda _scope: BotSkillLayoutState(
            scope=scope,
            active_layout=SkillLayout.POOL,
            target_layout=None,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            migration_generation="G1",
            persisted=True,
        ),
    )

    paths = injector.get(SkillServiceFactory).resolve_pool_paths(
        scope.entity_id,
        scope.bot_id,
        "hermes",
    )

    assert paths == (
        "/home/admin/.hermes/skills",
        "/home/admin/.hermes/workspace/skills-pool/skills-local",
        "/home/admin/.hermes/workspace/skills-pool/skills-repo",
    )


def test_skill_symlink_listener_uses_public_desktop_layout_state(
    monkeypatch,
) -> None:
    injector = build_injector(profile=DeployProfile.TEST)
    layout_repository = injector.get(SkillsPoolLayoutRepositoryProtocol)
    scope = BotSkillLayoutScope(
        env="pre",
        entity_id="staff_1",
        bot_id="desktop-1",
    )
    current = BotSkillLayoutState(
        scope=scope,
        active_layout=SkillLayout.POOL,
        target_layout=None,
        phase=SkillLayoutPhase.POOL_ACTIVE,
        migration_generation="G1",
        persisted=True,
    )
    monkeypatch.setattr(layout_repository, "get", lambda _scope: current)
    listener = injector.get(SkillSymlinkListener)
    authority = listener._desktop_layout_authority

    assert authority is not None
    assert authority({"bot_type": "service"}) is None
    assert authority({"bot_type": "desktop"}) is None
    bot = {
        "bot_type": "desktop",
        "env": scope.env,
        "entity_id": scope.entity_id,
        "bot_id": scope.bot_id,
    }
    assert authority(bot) == "pool"

    current = replace(
        current,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
    )
    assert authority(bot) == "transition"

    current = replace(
        current,
        target_layout=None,
        phase=SkillLayoutPhase.LEGACY_ACTIVE,
        migration_generation=None,
    )
    assert authority(bot) == "legacy"
