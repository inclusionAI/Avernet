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
from agentclaw.community.core.repository.implementations.skill_center.skill_center_reference import (
    SkillCenterReferenceRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.track_latest import (
    TrackLatestRepository,
)
from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.track_latest import (
    TrackLatestRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.group4_task_registrar import (
    SkillCenterGroup4TaskRegistrar,
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
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
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
