"""BotInventoryModule — production bindings for Bot inventory."""
from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_inventory_service import BotInventoryServiceProtocol
from agentclaw.community.api.local_bot_workflow_service import LocalBotWorkflowServiceProtocol
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.adapters.noop_service_lifecycle import (
    NoopServiceLifecyclePort,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryBotPort,
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
    ServiceLifecyclePort,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import BotLifecycleView
from agentclaw.community.core.bot_inventory.services.local_bot_workflow import LocalBotWorkflowService
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotService
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


class BotInventoryModule(Module):
    """Bindings for personal cloud/local Bot inventory read models."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BusinessSpaceContextProtocol,
            to=NoopBusinessSpaceContext,
            scope=singleton,
        )
        binder.bind(
            ServiceLifecyclePort,
            to=NoopServiceLifecyclePort,
            scope=singleton,
        )
        binder.bind(BotLifecycleView, to=BotLifecycleView, scope=singleton)
        binder.bind(BotInventoryBotPort, to=BotService, scope=singleton)
        binder.bind(DesktopBotInventoryPort, to=DesktopBotService, scope=singleton)

    @singleton
    @provider
    @inject
    def bot_inventory_service(
        self,
        bot_service: BotInventoryBotPort,
        desktop_service: DesktopBotInventoryPort,
        business_space: BusinessSpaceContextProtocol,
        lifecycle_view: BotLifecycleView,
    ) -> BotInventoryService:
        return BotInventoryService(
            bot_service=bot_service,
            desktop_service=desktop_service,
            business_space=business_space,
            lifecycle_view=lifecycle_view,
        )

    @singleton
    @provider
    @inject
    def bot_inventory_service_protocol(
        self, service: BotInventoryService
    ) -> BotInventoryServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def local_bot_workflow_service(
        self,
        desktop_service: DesktopBotInventoryPort,
        business_space: BusinessSpaceContextProtocol,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
    ) -> LocalBotWorkflowService:
        return LocalBotWorkflowService(
            desktop_service=desktop_service,
            business_space=business_space,
            passport_plugin=passport_plugin,
            auth_relationship_plugin=auth_relationship_plugin,
        )

    @singleton
    @provider
    @inject
    def local_bot_workflow_service_protocol(
        self, service: LocalBotWorkflowService
    ) -> LocalBotWorkflowServiceProtocol:
        return service
