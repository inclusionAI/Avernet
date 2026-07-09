"""Health concern — test / singlebox binding (LocalHealthProbe)."""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.health_probe import HealthProbePlugin


logger = get_logger()


class TestHealthModule(Module):
    """test / singlebox: LocalHealthProbe (LocalProcessManager-backed)."""

    @singleton
    @provider
    @inject
    def health_probe(
        self,
        binding_repo: DeviceBindingRepository,
        baas_service: BaasService,
        bot_repository: BotRepository,
    ) -> HealthProbePlugin:
        from agentclaw.community.plugins.local.health_probe import LocalHealthProbe

        logger.info("HealthProbePlugin: LocalHealthProbe (test)")
        return LocalHealthProbe(
            binding_repo=binding_repo,
            baas_service=baas_service,
            bot_repository=bot_repository,
        )
