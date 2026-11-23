"""DI bindings for ordinary projection recovery completion."""

from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.api.bot_runtime_projector import (
    BotRuntimeProjectorProtocol as ApiBotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.desktop_skill_recovery_protocol import (
    DesktopSkillRecoveryServiceProtocol,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityProjectionCompetitionProtocol,
    CapabilityProjectionReconcileProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol as CoreBotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.capability_delivery_guard import (
    CapabilityDeliveryGuard,
)
from agentclaw.community.core.skill_center.services.capability_projection_reconciler import (
    CapabilityProjectionReconciler,
)
from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
    RecoveringBotRuntimeProjector,
)
from agentclaw.community.di.manifest_apply_config import ManifestApplyConfig
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin


class RuntimeProjectionRecoveryModule(Module):
    """Expose the decorator normally while retaining the raw recovery executor."""

    @singleton
    @provider
    def manifest_apply_config(self) -> ManifestApplyConfig:
        config = read_user_config().get("manifest_apply", {})
        if not isinstance(config, dict):
            raise ValueError("manifest_apply must be a mapping")
        return ManifestApplyConfig(**config)

    @singleton
    @provider
    @inject
    def capability_desired_state_repository(
        self, db: DatabasePlugin,
    ) -> CapabilityDesiredStateRepository:
        return CapabilityDesiredStateRepository(db)

    @provider
    @inject
    def capability_desired_state_repository_protocol(
        self, repository: CapabilityDesiredStateRepository,
    ) -> CapabilityDesiredStateRepositoryProtocol:
        return repository

    @singleton
    @provider
    @inject
    def capability_projection_reconciler(
        self,
        projector: BotRuntimeProjector,
        cache: CachePlugin,
        bots: BotRepository,
        bindings: DeviceBindingRepository,
        config: ManifestApplyConfig,
    ) -> CapabilityProjectionReconciler:
        return CapabilityProjectionReconciler(
            projector=projector,
            guard=CapabilityDeliveryGuard(cache),
            bots=bots,
            bindings=bindings,
            defer_enabled=config.defer_capability_projection,
        )

    @singleton
    @provider
    @inject
    def capability_projection_reconcile_protocol(
        self, reconciler: CapabilityProjectionReconciler,
    ) -> CapabilityProjectionReconcileProtocol:
        return reconciler

    @singleton
    @provider
    @inject
    def capability_projection_competition_protocol(
        self, reconciler: CapabilityProjectionReconciler,
    ) -> CapabilityProjectionCompetitionProtocol:
        return reconciler

    @singleton
    @provider
    @inject
    def recovering_runtime_projection_reconciler(
        self,
        service: BotRuntimeProjector,
        recovery: DesktopSkillRecoveryServiceProtocol,
        capability_projection: CapabilityProjectionReconciler,
    ) -> RecoveringBotRuntimeProjector:
        return RecoveringBotRuntimeProjector(
            delegate=service,
            recovery=recovery,
            capability_projection=capability_projection,
        )

    @singleton
    @provider
    @inject
    def core_runtime_projection_reconciler_protocol(
        self, service: RecoveringBotRuntimeProjector
    ) -> CoreBotRuntimeProjectorProtocol:
        return service

    @singleton
    @provider
    @inject
    def api_runtime_projection_reconciler_protocol(
        self, service: RecoveringBotRuntimeProjector
    ) -> ApiBotRuntimeProjectorProtocol:
        return service


__all__ = ["RuntimeProjectionRecoveryModule"]
