"""BotPublicModule — production singletons for bot_public.

Replaces the factory shims in
``core/bot_public/dependencies/bot_public.py`` and the ad-hoc
``get_bot_discover_service()`` factory at the bottom of
``core/bot_public/services/bot_discover_service.py``.

``BotFriendRepositoryProtocol`` is the database-mode-keyed binding;
production binds the ZDAS impl. :class:`TestingBotPublicModule`
overrides it with the SQLite impl for local + test boots.

``ApprovalWorkflowPlugin`` is bound per profile by the infrastructure
approval-workflow column module (corp / community / test) —
:class:`BotPublicService` receives it through the injector via ``@inject``.

``BotPublicService`` and ``BotDiscoverService`` are mode-agnostic
``@singleton`` services; they pick up whichever repository is bound
through the injector.
"""
from __future__ import annotations

from typing import Annotated, Callable

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.repository.protocols.bot import BotFriendRepositoryProtocol
from agentclaw.community.core.bot_public.services.bot_discover_service import BotDiscoverService
from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService
from agentclaw.community.core.bot_public.services.bot_catalog_metadata_service import (
    BcsBotCatalogMetadataService,
)
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogMetadataServiceProtocol,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.bot_publish_approval import BotPublishApprovalPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.core.repository.implementations.bot.friend import BotFriendRepository as UnifiedBotFriendRepository
from agentclaw.community.plugin_api.http_client import QUALIFIER_BCN, HttpClient
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher


logger = get_logger()


class BotPublicModule(Module):
    """Production bindings for bot_public."""

    def configure(self, binder: Binder) -> None:
        # ``BotDiscoverService`` and ``BotPublicService`` both type a
        # di-owned collaborator under TYPE_CHECKING to break a
        # core.bot_public -> agentclaw.community.di import cycle, so both must be
        # explicit @providers (``binder.bind`` would call
        # ``get_type_hints`` on their ``__init__`` and NameError on the
        # TYPE_CHECKING-only annotation). See the providers below.
        # Single unified ORM impl — runs on prod OceanBase and SQLite
        # via the injected DatabasePlugin (@inject ctor).
        binder.bind(
            BotFriendRepositoryProtocol,
            to=UnifiedBotFriendRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def catalog_metadata_service(
        self,
        http_client: Annotated[HttpClient, QUALIFIER_BCN],
    ) -> BotCatalogMetadataServiceProtocol:
        return BcsBotCatalogMetadataService(http_client=http_client)

    @singleton
    @provider
    def bot_discover_service(
        self,
        bot_repository: BotRepository,
        bcsfuse_config: cfg.BcsFuseConfig,
        catalog_metadata_service: BotCatalogMetadataServiceProtocol,
    ) -> BotDiscoverService:
        # Explicit provider (not ``binder.bind``): BotDiscoverService
        # types ``bcsfuse_config`` under TYPE_CHECKING to avoid a
        # bot_discover_service -> agentclaw.community.di runtime import cycle, so
        # ``get_type_hints`` on its ``__init__`` would NameError.
        # Constructing it here keeps the injector from inspecting its
        # annotations.
        return BotDiscoverService(
            bot_repository=bot_repository,
            bcsfuse_config=bcsfuse_config,
            catalog_metadata_service=catalog_metadata_service,
        )

    @singleton
    @provider
    @inject
    def device_context_resolver_factory(
        self, injector: Injector
    ) -> Callable[[], DeviceContextResolver]:
        # Lazy (cycle-safe): DeviceContextResolver's conn-info builders reach
        # DeviceService, which in some profiles depends on BotPublicService
        # itself (BotPublicService -> DeviceContextResolver -> ... ->
        # DeviceService -> BotPublicService). Eager injection would cycle.
        return lambda: injector.get(DeviceContextResolver)

    @singleton
    @provider
    def bot_public_service(
        self,
        bot_friend_repo: BotFriendRepositoryProtocol,
        bot_repository: BotRepository,
        process_service: ApprovalWorkflowPlugin,
        bot_service: BotService,
        bcn_service: BcnService,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        publish_approval_plugin: BotPublishApprovalPlugin,
        skill_set_service_factory: SkillSetServiceFactory,
        device_context_resolver_factory: Callable[[], DeviceContextResolver],
        device_sync_dispatcher: DeviceSyncDispatcher,
        bcsfuse_config: cfg.BcsFuseConfig,
        catalog_metadata_service: BotCatalogMetadataServiceProtocol,
    ) -> BotPublicService:
        # Explicit provider (not ``binder.bind``): BotPublicService types
        # ``skill_set_service_factory`` under TYPE_CHECKING to avoid a
        # bot_public_service -> agentclaw.community.di runtime import cycle, so
        # ``get_type_hints`` on its ``__init__`` would NameError.
        return BotPublicService(
            bot_friend_repo=bot_friend_repo,
            bot_repository=bot_repository,
            process_service=process_service,
            bot_service=bot_service,
            bcn_service=bcn_service,
            passport_plugin=passport_plugin,
            auth_relationship_plugin=auth_relationship_plugin,
            publish_approval_plugin=publish_approval_plugin,
            skill_set_service_factory=skill_set_service_factory,
            device_context_resolver_factory=device_context_resolver_factory,
            device_sync_dispatcher=device_sync_dispatcher,
            bcsfuse_config=bcsfuse_config,
            catalog_metadata_service=catalog_metadata_service,
        )

    @singleton
    @provider
    @inject
    def _bot_public_service_protocol(self, svc: BotPublicService) -> BotPublicServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _bot_discover_service_protocol(self, svc: BotDiscoverService) -> BotDiscoverServiceProtocol:
        return svc
