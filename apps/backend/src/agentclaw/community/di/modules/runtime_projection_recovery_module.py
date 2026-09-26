"""DI bindings for ordinary projection recovery completion."""

from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.api.bot_runtime_projector import (
    BotRuntimeProjectorProtocol as ApiBotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.desktop_skill_recovery_protocol import (
    DesktopSkillRecoveryServiceProtocol,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol as CoreBotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
    RecoveringBotRuntimeProjector,
)


class RuntimeProjectionRecoveryModule(Module):
    """Expose the decorator normally while retaining the raw recovery executor."""

    @singleton
    @provider
    @inject
    def recovering_runtime_projection_reconciler(
        self,
        service: BotRuntimeProjector,
        recovery: DesktopSkillRecoveryServiceProtocol,
    ) -> RecoveringBotRuntimeProjector:
        return RecoveringBotRuntimeProjector(
            delegate=service,
            recovery=recovery,
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
