"""DI bindings for ordinary projection recovery completion."""

from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.api.bot_runtime_projector import (
    BotRuntimeProjectorProtocol as ApiBotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.desktop_skill_recovery_protocol import (
    DesktopSkillRecoveryServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_projection import (
    CapabilityProjectionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
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
    def capability_projection_reconciler(
        self,
        repository: CapabilityProjectionRepositoryProtocol,
        projector: BotRuntimeProjector,
        cache: CachePlugin,
        bots: BotRepository,
        config: ManifestApplyConfig,
    ) -> CapabilityProjectionReconciler:
        return CapabilityProjectionReconciler(
            repository=repository,
            projector=projector,
            guard=CapabilityDeliveryGuard(cache),
            bots=bots,
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
