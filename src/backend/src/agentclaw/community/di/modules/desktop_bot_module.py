"""DesktopBotModule — production bindings for desktop_bot."""

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.desktop_bot_service import DesktopBotServiceProtocol
from agentclaw.community.core.desktop_bot.lifecycle import DesktopBotLifecycle
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotService
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.di.config import BaasConfig
from agentclaw.community.plugin_api.passport import PassportPlugin


class DesktopBotModule(Module):
    """Production bindings for desktop_bot.

    Uses an explicit @provider method so all dependencies (including DeviceService)
    are resolved through the provider rather than relying on @inject ctor.
    """

    def configure(self, binder: Binder) -> None:
        binder.bind(DesktopBotLifecycle, to=DesktopBotLifecycle, scope=singleton)

    @singleton
    @provider
    @inject
    def desktop_bot_service(
        self,
        baas_service: BaasService,
        passport_plugin: PassportPlugin,
        device_binding_repo: DeviceBindingRepository,
        bot_repository: BotRepository,
        baas_config: BaasConfig,
        device_service: DeviceService,
        skill_set_factory: SkillSetServiceFactory,
    ) -> DesktopBotService:
        return DesktopBotService(
            baas_service=baas_service,
            passport_plugin=passport_plugin,
            device_binding_repo=device_binding_repo,
            bot_repository=bot_repository,
            baas_config=baas_config,
            device_service=device_service,
            skill_set_factory=skill_set_factory,
        )

    @singleton
    @provider
    @inject
    def _desktop_bot_service_protocol(self, svc: DesktopBotService) -> DesktopBotServiceProtocol:
        return svc
