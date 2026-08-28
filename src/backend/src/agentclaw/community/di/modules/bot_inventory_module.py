"""BotInventoryModule — production bindings for Bot inventory."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.bot_inventory_service import BotInventoryServiceProtocol
from agentclaw.community.api.local_bot_workflow_service import (
    LocalBotWorkflowServiceProtocol,
)
from agentclaw.community.adapters.bot_space_context import (
    SpaceServiceBotSpaceContext,
)
from agentclaw.community.core.bot_inventory.adapters.service_lifecycle import (
    ServiceLifecycleView,
)
from agentclaw.community.core.bot_inventory.adapters.service_edit_lock import (
    ServiceEditLockView,
)
from agentclaw.community.core.bot_inventory.adapters.template_page import (
    TemplateServiceInventoryTemplatePort,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BotInventoryAccessPort,
    BotInventoryBotPort,
    BotInventoryTemplatePort,
    BusinessSpaceContextProtocol,
    DesktopBotInventoryPort,
    ServiceEditLockPort,
    ServiceLifecyclePort,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import (
    BotLifecycleView,
)
from agentclaw.community.core.bot_inventory.services.local_bot_workflow import (
    LocalBotWorkflowService,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotService,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


class BotInventoryModule(Module):
    """Bindings for personal cloud/local Bot inventory read models."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            BusinessSpaceContextProtocol,
            to=SpaceServiceBotSpaceContext,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def service_lifecycle(
        self, publish_repo: BotPublishRepositoryProtocol
    ) -> ServiceLifecyclePort:
        return ServiceLifecycleView(publish_repo)

    @singleton
    @provider
    @inject
    def lifecycle_view(
        self, service_lifecycle: ServiceLifecyclePort
    ) -> BotLifecycleView:
        return BotLifecycleView(service_lifecycle)

    @singleton
    @provider
    @inject
    def service_edit_lock_view(
        self,
        collaborator_repo: CollaboratorRepositoryProtocol,
        lock_repo: BotCollabLockRepositoryProtocol,
    ) -> ServiceEditLockPort:
        return ServiceEditLockView(collaborator_repo, lock_repo)

    @singleton
    @provider
    @inject
    def inventory_bot_port(self, service: BotService) -> BotInventoryBotPort:
        return service

    @singleton
    @provider
    @inject
    def desktop_inventory_port(
        self, service: DesktopBotService
    ) -> DesktopBotInventoryPort:
        return service

    @singleton
    @provider
    @inject
    def inventory_access_port(
        self, service: CollaboratorService
    ) -> BotInventoryAccessPort:
        return service

    @singleton
    @provider
    @inject
    def inventory_template_port(
        self, template_service: TemplateService
    ) -> BotInventoryTemplatePort:
        return TemplateServiceInventoryTemplatePort(template_service)

    @singleton
    @provider
    @inject
    def bot_inventory_service(
        self,
        bot_service: BotInventoryBotPort,
        desktop_service: DesktopBotInventoryPort,
        access_service: BotInventoryAccessPort,
        business_space: BusinessSpaceContextProtocol,
        lifecycle_view: BotLifecycleView,
        edit_lock_view: ServiceEditLockPort,
        template_port: BotInventoryTemplatePort,
    ) -> BotInventoryService:
        return BotInventoryService(
            bot_service=bot_service,
            desktop_service=desktop_service,
            access_service=access_service,
            business_space=business_space,
            lifecycle_view=lifecycle_view,
            edit_lock_view=edit_lock_view,
            template_port=template_port,
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
