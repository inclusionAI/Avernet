"""BotCollaboratorModule — production bindings for the bot_collaborator module.

Bindings:

- ``CollaboratorRepositoryProtocol`` — binds to the unified ``CollaboratorRepository``
  which uses ``DatabasePlugin.orm_session()`` and works for both SQLite and ZDAS.
- ``BotCollabLogRepositoryProtocol`` — binds to ``BotCollabLogRepository``.
- ``BotCollabLockRepositoryProtocol`` — binds to ``BotCollabLockRepository``.
- ``CollaboratorService`` — plain class, providers wire it up.
- ``CollaboratorLockService`` — plain class, providers wire it up.

Tests construct services directly with mocks (no fake services); the
bindings exist so production routes can use ``Injected(...)``.
"""
from __future__ import annotations

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.collaborator_lock_service import CollaboratorLockServiceProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.bot_collaborator.protocols import (
    BotServiceProtocol,
    CollaboratorServiceProtocol as CoreCollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
    BotCollabLogRepositoryProtocol,
    BotCollabLockRepositoryProtocol,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import CollaboratorService
from agentclaw.community.core.bot_collaborator.services.aicoding.member_management_capability import (
    AICodingMemberManagementCapability,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugins.bot_collaborator_repository import CollaboratorRepository
from agentclaw.community.plugins.bot_collab_log_repository import BotCollabLogRepository
from agentclaw.community.plugins.bot_collab_lock_repository import BotCollabLockRepository


class BotCollaboratorModule(Module):
    """Bindings for bot_collaborator - unified repository works for both SQLite and ZDAS."""

    def configure(self, binder: Binder) -> None:
        # Bind the unified repository implementations
        binder.bind(
            CollaboratorRepositoryProtocol,
            to=CollaboratorRepository,
            scope=singleton,
        )
        binder.bind(
            BotCollabLogRepositoryProtocol,
            to=BotCollabLogRepository,
            scope=singleton,
        )
        binder.bind(
            BotCollabLockRepositoryProtocol,
            to=BotCollabLockRepository,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def collaborator_lock_service(
        self,
        lock_repo: BotCollabLockRepositoryProtocol,
        collab_service: CoreCollaboratorServiceProtocol,
        bot_service: BotServiceProtocol,
    ) -> CollaboratorLockService:
        """Construct ``CollaboratorLockService``."""
        return CollaboratorLockService(
            lock_repo=lock_repo,
            collab_service=collab_service,
            bot_service=bot_service,
        )

    @singleton
    @provider
    @inject
    def member_management_capability_service(
        self,
        template_service: TemplateService,
    ) -> MemberManagementCapabilityService:
        """Construct engine-agnostic member-management capability coordinator."""
        return MemberManagementCapabilityService(
            template_service=template_service,
            engine_capabilities=(AICodingMemberManagementCapability(),),
        )

    @singleton
    @provider
    @inject
    def collaborator_service(
        self,
        collaborator_repo: CollaboratorRepositoryProtocol,
        bot_repo: BotRepository,
        passport_plugin: PassportPlugin,
        member_management_capability_service: MemberManagementCapabilityService,
        injector: Injector,
    ) -> CollaboratorService:
        """Construct ``CollaboratorService``.

        ``resolver`` / ``device_fs_dispatcher`` 走 lazy ``Callable`` thunk 注入，
        打断构造期 DI 循环(device 图反向依赖 ``BotService``)。同手法见
        ``di/modules/mcp_module.py`` 的 ``mcp_sync_service``。
        """
        return CollaboratorService(
            collaborator_repo=collaborator_repo,
            bot_repo=bot_repo,
            passport_plugin=passport_plugin,
            resolver_provider=lambda: injector.get(DeviceContextResolver),
            device_fs_dispatcher_provider=lambda: injector.get(DeviceFilesystemDispatcher),
            member_management_capability_service=member_management_capability_service,
        )

    # ── Core Protocol aliases (for core layer internal use) ───────────────

    @singleton
    @provider
    @inject
    def _core_collaborator_service_protocol(
        self, svc: CollaboratorService
    ) -> CoreCollaboratorServiceProtocol:
        """Bind core layer's CollaboratorServiceProtocol."""
        return svc

    # ── Service API Protocol aliases (for api/layer use) ────────────────

    @singleton
    @provider
    @inject
    def _collaborator_service_protocol(
        self, svc: CollaboratorService
    ) -> CollaboratorServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def _collaborator_lock_service_protocol(
        self, svc: CollaboratorLockService
    ) -> CollaboratorLockServiceProtocol:
        return svc
