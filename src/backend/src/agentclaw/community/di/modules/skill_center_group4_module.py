"""Composition-root bindings for SC Public Reference, Sync, and Track Latest."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_capability_state_reader import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.api.bot_runtime_projector import BotRuntimeProjectorProtocol
from agentclaw.community.api.skill_center_reference_service import (
    SkillCenterReferenceServiceProtocol,
)
from agentclaw.community.api.skill_center_sync_service import (
    SkillCenterSyncServiceProtocol,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.api.skill_version_materializer import (
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.api.track_latest import TrackLatestServiceProtocol
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.events.types import (
    DeviceActivatedEvent,
    DeviceAliveEvent,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_center_reference import (
    SkillCenterReferenceRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.track_latest import (
    TrackLatestRepository,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.track_latest import (
    TrackLatestRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.group4_task_registrar import (
    SkillCenterGroup4TaskRegistrar,
)
from agentclaw.community.core.skill_center.services.lifecycle_runtime_reprojection import (
    LifecycleRuntimeProjectionTaskHandler,
    LifecycleRuntimeProjectionWakeup,
)
from agentclaw.community.core.skill_center.services.mcp_runtime_probe import (
    CurrentMcpRuntimeProbeService,
)
from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
    EngineRuntimeProjectionRegistry,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_processor import (
    SkillCenterReferenceProcessor,
    SkillCenterReferenceTaskHandler,
)
from agentclaw.community.core.skill_center.services.skill_center_reference_service import (
    SkillCenterReferenceService,
)
from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
    SkillCenterSyncService,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    BotTrackLatestReconcileTaskHandler,
    TrackLatestFanoutTaskHandler,
    TrackLatestService,
)
from agentclaw.community.core.skill_center.services.track_latest_event_listener import (
    TrackLatestPublishedVersionListener,
)
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.core.skills_pool.reconcile_task import (
    SkillsPoolReconcileWakeupListener,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    runtime_uses_pool_paths,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)


class SkillCenterGroup4Module(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(
            SkillCenterReferenceRepositoryProtocol,
            to=SkillCenterReferenceRepository,
            scope=singleton,
        )
        binder.bind(
            TrackLatestRepositoryProtocol,
            to=TrackLatestRepository,
            scope=singleton,
        )

    @singleton
    @provider
    def current_mcp_runtime_probe_service(
        self,
        resolver: DeviceContextResolver,
        adapter_transport: DeviceAdapterTransport,
    ) -> CurrentMcpRuntimeProbeService:
        return CurrentMcpRuntimeProbeService(
            resolver=resolver,
            adapter_transport=adapter_transport,
        )

    @singleton
    @provider
    def lifecycle_runtime_projection_task_handler(
        self,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        projection_registry: EngineRuntimeProjectionRegistry,
        runtime_reconciler: BotRuntimeProjectorProtocol,
        mcp_probe: CurrentMcpRuntimeProbeService,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        skills_pool_wakeup: SkillsPoolReconcileWakeupListener,
    ) -> LifecycleRuntimeProjectionTaskHandler:
        def skill_projection_authority(bot: dict[str, object]) -> str | None:
            if bot.get("bot_type") != "desktop":
                return None
            values = (bot.get("env"), bot.get("entity_id"), bot.get("bot_id"))
            if not all(isinstance(value, str) and value for value in values):
                return None
            state = layout_repository.get(
                BotSkillLayoutScope(
                    env=str(values[0]),
                    entity_id=str(values[1]),
                    bot_id=str(values[2]),
                )
            )
            if state.active_layout is SkillLayout.POOL:
                return "pool"
            return "transition" if runtime_uses_pool_paths(state) else "legacy"

        def wake_skills_pool(
            bot: dict[str, object], binding: DeviceBindingRecord
        ) -> None:
            event_type = (
                DeviceActivatedEvent
                if binding.device_provider == "baas"
                else DeviceAliveEvent
            )
            skills_pool_wakeup.handle(
                event_type(
                    device_id=binding.device_id,
                    binding_id=binding.id,
                    entity_id=binding.entity_id,
                    entity_type=binding.entity_type,
                    device_provider=binding.device_provider,
                    sandbox_id=(binding.device_props or {}).get("sandbox_id"),
                )
            )

        return LifecycleRuntimeProjectionTaskHandler(
            binding_repository=binding_repository,
            bot_repository=bot_repository,
            projection_registry=projection_registry,
            projector=runtime_reconciler,
            mcp_probe=mcp_probe,
            skill_projection_authority=skill_projection_authority,
            skills_pool_reconcile_wakeup=wake_skills_pool,
        )

    @singleton
    @provider
    def lifecycle_runtime_projection_wakeup(
        self,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        task_queue_service: TaskQueueService,
        projection_registry: EngineRuntimeProjectionRegistry,
        registry: HandlerRegistry,
        task_handler: LifecycleRuntimeProjectionTaskHandler,
    ) -> LifecycleRuntimeProjectionWakeup:
        return LifecycleRuntimeProjectionWakeup(
            binding_repository=binding_repository,
            bot_repository=bot_repository,
            task_queue_service=task_queue_service,
            projection_registry=projection_registry,
            registry=registry,
            task_handler=task_handler,
        )

    @singleton
    @provider
    @inject
    def track_latest_service(
        self, tasks: TaskQueueService
    ) -> TrackLatestServiceProtocol:
        return TrackLatestService(tasks)

    @singleton
    @provider
    @inject
    def track_latest_published_version_listener(
        self, track_latest: TrackLatestServiceProtocol
    ) -> TrackLatestPublishedVersionListener:
        return TrackLatestPublishedVersionListener(track_latest)

    @singleton
    @provider
    @inject
    def reference_service(
        self,
        references: SkillCenterReferenceRepositoryProtocol,
        skill_sets: SkillSetManagementServiceProtocol,
        tasks: TaskQueueService,
    ) -> SkillCenterReferenceServiceProtocol:
        return SkillCenterReferenceService(
            references=references, skill_sets=skill_sets, tasks=tasks
        )

    @singleton
    @provider
    @inject
    def reference_processor(
        self,
        references: SkillCenterReferenceRepositoryProtocol,
        gateway: SkillCenterGatewayServiceProtocol,
        materializer: SkillVersionMaterializerProtocol,
        skill_sets: SkillSetManagementServiceProtocol,
        track_latest: TrackLatestServiceProtocol,
    ) -> SkillCenterReferenceProcessor:
        return SkillCenterReferenceProcessor(
            references=references,
            gateway=gateway,
            materializer=materializer,
            skill_sets=skill_sets,
            track_latest=track_latest,
        )

    @singleton
    @provider
    @inject
    def reference_task_handler(
        self, processor: SkillCenterReferenceProcessor
    ) -> SkillCenterReferenceTaskHandler:
        return SkillCenterReferenceTaskHandler(processor)

    @singleton
    @provider
    @inject
    def fanout_task_handler(
        self,
        candidates: TrackLatestRepositoryProtocol,
        tasks: TaskQueueService,
    ) -> TrackLatestFanoutTaskHandler:
        return TrackLatestFanoutTaskHandler(candidates=candidates, tasks=tasks)

    @singleton
    @provider
    @inject
    def reconcile_task_handler(
        self,
        reader: BotCapabilityStateReaderProtocol,
        projector: BotRuntimeProjectorProtocol,
        latest: TrackLatestRepositoryProtocol,
    ) -> BotTrackLatestReconcileTaskHandler:
        return BotTrackLatestReconcileTaskHandler(
            reader=reader, projector=projector, latest=latest
        )

    @singleton
    @provider
    @inject
    def task_registrar(
        self,
        registry: HandlerRegistry,
        reference: SkillCenterReferenceTaskHandler,
        fanout: TrackLatestFanoutTaskHandler,
        reconcile: BotTrackLatestReconcileTaskHandler,
    ) -> SkillCenterGroup4TaskRegistrar:
        return SkillCenterGroup4TaskRegistrar(
            registry=registry,
            reference=reference,
            fanout=fanout,
            reconcile=reconcile,
        )

    @singleton
    @provider
    @inject
    def sync_service(
        self,
        assets: SkillCenterReferenceRepositoryProtocol,
        gateway: SkillCenterGatewayServiceProtocol,
        materializer: SkillVersionMaterializerProtocol,
        track_latest: TrackLatestServiceProtocol,
        cache: CachePlugin,
    ) -> SkillCenterSyncService:
        return SkillCenterSyncService(
            assets=assets,
            gateway=gateway,
            materializer=materializer,
            track_latest=track_latest,
            cache=cache,
        )

    @singleton
    @provider
    @inject
    def sync_service_protocol(
        self, service: SkillCenterSyncService
    ) -> SkillCenterSyncServiceProtocol:
        return service


__all__ = ["SkillCenterGroup4Module"]
