"""Skills Pool 控制面业务边界的 DI 装配测试。"""

from dataclasses import replace
from unittest.mock import MagicMock


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
from agentclaw.community.core.events.types import RuntimeProjectionRequestedEvent
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
    SkillSymlinkListener,
)
from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
    PerDomainRuntimeProjection,
)
from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import (
    SkillRuntimeDelivery,
)
from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
    WholeArtifactRuntimeProjection,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.core.skill_center.center_content_distribution import CenterContentDistribution
from agentclaw.community.core.skills_pool.runtime import OpenClawSkillsPoolRuntime
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules.skill_center_module import SkillCenterModule
from agentclaw.community.core.repository.implementations.skills_pool.layout import SkillsPoolLayoutRepository
from agentclaw.community.core.repository.implementations.skill_center.skill import SkillRepository


def test_skill_runtime_delivery_is_wired_only_into_per_domain_projection() -> None:
    pool_runtime = MagicMock(spec=SkillsPoolRuntimeProtocol)
    pool_layouts = MagicMock(spec=SkillsPoolLayoutRepositoryProtocol)
    device_contexts = MagicMock(spec=DeviceContextResolver)
    center_content = MagicMock(spec=CenterContentDistribution)

    registry = SkillCenterModule().engine_runtime_projection_registry(
        pool_runtime=pool_runtime,
        pool_layouts=pool_layouts,
        device_contexts=device_contexts,
        center_content=center_content,
    )

    per_domain = registry.for_engine("openclaw")
    assert isinstance(per_domain, PerDomainRuntimeProjection)
    assert isinstance(per_domain._skill_delivery, SkillRuntimeDelivery)
    assert per_domain._skill_delivery._pool_runtime is pool_runtime
    assert per_domain._skill_delivery._pool_layouts is pool_layouts
    assert per_domain._skill_delivery._device_contexts is device_contexts
    assert per_domain._skill_delivery._center_content is center_content
    assert isinstance(registry.for_engine("teclaw"), WholeArtifactRuntimeProjection)


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


def test_skill_symlink_listener_projects_everything_for_runtime_ready_events() -> None:
    runtime_reconciler = MagicMock()
    bot_repo = MagicMock()
    bot_repo.get_by_binding_id.return_value = {
        "bot_id": "service-1",
        "owner_id": "owner-1",
        "bot_type": "service",
    }
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value.binding_id = 42
    layout_repository = MagicMock()
    module = SkillCenterModule()
    listener = module.skill_symlink_listener(
        bot_repo=bot_repo,
        skill_set_factory=MagicMock(),
        resolver=resolver,
        device_sync_dispatcher=MagicMock(),
        layout_repository=layout_repository,
        skills_pool_wakeup=MagicMock(),
        desktop_skill_recovery=MagicMock(),
        runtime_reconciler=runtime_reconciler,
    )

    listener.handle(
        RuntimeProjectionRequestedEvent(
            device_id="device-1",
            binding_id=42,
            entity_id="owner-1",
            entity_type="staff",
            device_provider="baas",
        )
    )

    runtime_reconciler.project.assert_called_once_with(
        bot_id="service-1",
        owner_id="owner-1",
        scope=ProjectionScope.everything(),
    )
    layout_repository.get.assert_not_called()


@pytest.mark.parametrize("template_type", ("personalCoding", "applicationCoding"))
def test_coding_template_pool_paths_use_aicoding_physical_layout(
    monkeypatch,
    template_type: str,
) -> None:
    """Pool Local upload/replace/delete must never address .claude_code."""

    injector = build_injector(profile=DeployProfile.TEST)
    bot_repository = injector.get(BotRepository)
    layout_repository = injector.get(SkillsPoolLayoutRepositoryProtocol)
    scope = BotSkillLayoutScope(env="pre", entity_id="staff_1", bot_id="coding-1")
    monkeypatch.setattr(
        bot_repository,
        "get_by_id_and_owner",
        lambda *_: {
            "bot_id": scope.bot_id,
            "owner_id": "owner-1",
            "entity_id": scope.entity_id,
            "env": scope.env,
            "bot_type": "personal",
            "active_engine": "claude_code",
            "template_type": template_type,
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

    factory = injector.get(SkillServiceFactory)
    factory._device_fs_dispatcher.for_bot = lambda *_: object()
    paths = factory.resolve_pool_paths("owner-1", scope.bot_id, "claude_code")
    assert paths == (
        "/home/admin/.claude/skills",
        "/home/admin/.aicoding/workspace/skills-pool/skills-local",
        "/home/admin/.aicoding/workspace/skills-pool/skills-repo",
    )

    local_directory, upload_storage = factory.local_skill_package_storage(
        entity_id=scope.entity_id,
        owner_id="owner-1",
        bot_id=scope.bot_id,
        engine_type="claude_code",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        name="replacement",
    )
    delete_storage = factory.local_skill_package_storage_for_locator(
        entity_id=scope.entity_id,
        owner_id="owner-1",
        bot_id=scope.bot_id,
        engine_type="claude_code",
        entity_type="staff",
        is_desktop=False,
        is_teclaw=False,
        locator="replacement",
        skill_name="replacement",
    )
    expected = "/home/admin/.aicoding/workspace/skills-pool/skills-local/replacement"
    assert local_directory == expected
    assert upload_storage._device_directory == expected
    assert delete_storage._device_directory == expected



@pytest.mark.asyncio
@pytest.mark.parametrize(("engine", "names"), [
    ("claude_code", ("worktime-tracker", "featurepro-knowledge-flow", "jar-publish", "aix-support")),
    ("openclaw", tuple(f"repo-{i}" for i in range(8)) + ("learn-method", "antwork")),
])
async def test_composed_skill_delivery_reaches_transport_when_cache_locks_fail(
    test_injector, monkeypatch, engine, names,
):
    from unittest.mock import AsyncMock

    from agentclaw.community.core.devices.services.device_context import DeviceContext
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        ResolvedSkillPlan, RuntimeProjectionStatus,
    )
    from agentclaw.community.core.skill_center.runtime_resolver import RuntimeProjectionResolver
    from agentclaw.community.core.skill_center.services.runtime_projections.registry import EngineRuntimeProjectionRegistry
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset
    from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError, CachePlugin
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport

    cache = test_injector.get(CachePlugin)
    locks = {}
    for method in ("acquire_lock_strict", "renew_lock_strict"):
        locks[method] = MagicMock(side_effect=CacheLockInfrastructureError("cache unavailable"))
        monkeypatch.setattr(cache, method, locks[method])
    context = DeviceContext(
        provider="baas", conn_info={"binding_id": 19}, binding_id=19,
        bot_id="bot-1", user_id="owner-1", bot_type="personal",
    )
    monkeypatch.setattr(test_injector.get(DeviceContextResolver), "resolve_for_bot", lambda *_: context)
    monkeypatch.setattr(test_injector.get(SkillsPoolLayoutRepositoryProtocol), "get", lambda _: None)
    assets = tuple(RegisteredSkillAsset(
        skill_id=i + 1, name=name, git_path=f"git://infra/{name}",
    ) for i, name in enumerate(names))
    plan = ResolvedSkillPlan(
        bot_id="bot-1", owner_id="owner-1", engine=engine,
        bot={"env": "test", "entity_id": "owner-1", "active_engine": engine},
        projection=RuntimeProjectionResolver().resolve_skills(assets),
    )
    async def respond(_conn, method, path, *, body, **_kwargs):
        return {"success": True, "data": {"status": "CONVERGED", "items": [
            {"mapping": mapping, "action": "APPLY", "status": "CONVERGED",
             "target": f"/skills/{mapping['link_name']}", "retryable": False}
            for mapping in body["mappings"]
        ], "issues": [], "evidence": {}}}
    invoke = AsyncMock(side_effect=respond)
    monkeypatch.setattr(test_injector.get(DeviceAdapterTransport), "invoke", invoke)
    result = await test_injector.get(EngineRuntimeProjectionRegistry).for_engine(engine).apply(
        plan=plan, scope=ProjectionScope(skills=True), service_factory=MagicMock(),
    )
    assert result.status is RuntimeProjectionStatus.CONVERGED
    invoke.assert_awaited_once()
    assert invoke.call_args.args[1:3] == ("POST", "/api/skills/mappings/apply")
    assert [item["link_name"] for item in invoke.call_args.kwargs["body"]["mappings"]] == list(names)
    for lock in locks.values():
        lock.assert_not_called()
