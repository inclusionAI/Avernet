"""Production-safe bindings for Caller identity domain services."""

from __future__ import annotations

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
)
from agentclaw.community.api.caller_credential import (
    CallerRuntimeUpdater,
    CallerTokenProvider,
    UnavailableCallerTokenProvider,
)
from agentclaw.community.api.caller_iam_token_service import (
    CallerIamTokenServiceProtocol,
)
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_collaborator.collaborator_service_protocol import (
    CollaboratorServiceProtocol as CoreCollaboratorServiceProtocol,
)
from agentclaw.community.core.caller_identity.protocols import (
    CallerIdentityTokenExchangeProtocol,
    CallerRuntimeUpdaterProtocol,
    CallerTokenProviderProtocol,
)
from agentclaw.community.core.caller_identity.protocols import (
    BcsCallerConfigSyncProtocol,
)
from agentclaw.community.core.caller_identity.contracts import CallerBcsSyncError
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError
from agentclaw.community.log import get_logger
from agentclaw.community.core.repository.protocols.identity import CallerIdentityRepositoryProtocol
from agentclaw.community.core.caller_identity.session_validator import CallerSessionValidator
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.caller_identity.service import CallerIdentityService
from agentclaw.community.core.caller_identity.iam_token_service import (
    CallerIamTokenService,
)
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.core.repository.implementations.identity.caller_identity import CallerIdentityRepository
from agentclaw.community.core.token_exchange import NoopTokenPluginPipeline, TokenExchangeOrchestrator, TokenPluginPipeline
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


class BcnBackedCallerConfigSync:
    """Adapt BcnService transport outcomes onto the caller sync port.

    transport 的 BcnServiceError 在端口层折叠为域错误 CallerBcsSyncError,
    交由调用方(更新操作)整体失败传播;skipped 是 env 门降级,静默成功。
    """

    def __init__(self, bcn_service: BcnService) -> None:
        self._bcn_service = bcn_service

    def sync_caller_config(
        self,
        *,
        bot_id: str,
        owner_id: str,
        enabled: bool,
        source_revision: int,
    ) -> None:
        logger = get_logger()
        try:
            result = self._bcn_service.sync_bot_caller_config(
                teamclaw_bot_uuid=bot_id,
                owner_workno=owner_id,
                enabled=enabled,
                source_revision=source_revision,
            )
        except BcnServiceError as exc:
            logger.warning(
                "caller_config_bcs_sync_failed bot_id=%s owner_id=%s "
                "enabled=%s source_revision=%s error=%s",
                bot_id,
                owner_id,
                enabled,
                source_revision,
                exc,
            )
            raise CallerBcsSyncError from exc
        if result.get("skipped"):
            logger.info(
                "caller_config_bcs_sync_skipped bot_id=%s owner_id=%s "
                "enabled=%s source_revision=%s",
                bot_id,
                owner_id,
                enabled,
                source_revision,
            )
        else:
            logger.info(
                "caller_config_bcs_sync_succeeded bot_id=%s owner_id=%s "
                "enabled=%s source_revision=%s sync_status=%s",
                bot_id,
                owner_id,
                enabled,
                source_revision,
                result.get("sync_status"),
            )


class CallerIdentityModule(Module):
    """Wire the unified repository and fail-closed domain service."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            CallerIdentityRepositoryProtocol,
            to=CallerIdentityRepository,
            scope=singleton,
        )
        binder.bind(
            CallerTokenProvider,
            to=UnavailableCallerTokenProvider,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def caller_session_validator(
        self, binding_repository: DeviceBindingRepository,
        runtime_bindings: RuntimeBindingResolutionService,
    ) -> CallerSessionValidator:
        return CallerSessionValidator(binding_repository=binding_repository, runtime_bindings=runtime_bindings)

    @singleton
    @provider
    @inject
    def bcs_caller_config_sync(
        self,
        bcn_service: BcnService,
    ) -> BcsCallerConfigSyncProtocol:
        """Bind the BcnService-backed adapter to the caller sync port."""
        return BcnBackedCallerConfigSync(bcn_service)

    @singleton
    @provider
    @inject
    def caller_identity_service(
        self,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
        lock_repository: BotCollabLockRepositoryProtocol,
        mcp_provider: BotMCPProvider,
        repository: CallerIdentityRepositoryProtocol,
        mcp_sync_service: MCPSyncServiceProtocol,
        passport_plugin: PassportPlugin,
        collaborator_service: CoreCollaboratorServiceProtocol,
        session_validator: CallerSessionValidator,
        bcs_caller_sync: BcsCallerConfigSyncProtocol,
    ) -> CallerIdentityService:
        """Construct the draft configuration and Agent Principal sync service."""
        return CallerIdentityService(
            bot_repository=bot_repository,
            collaborator_repository=collaborator_repository,
            lock_repository=lock_repository,
            mcp_provider=mcp_provider,
            repository=repository,
            mcp_sync_service=mcp_sync_service,
            passport_plugin=passport_plugin,
            collaborator_service=collaborator_service,
            session_validator=session_validator,
            bcs_caller_sync=bcs_caller_sync,
        )

    @singleton
    @provider
    @inject
    def caller_identity_service_protocol(
        self,
        service: CallerIdentityService,
    ) -> CallerIdentityServiceProtocol:
        return service

    @singleton
    @provider
    @inject
    def caller_identity_token_exchange_protocol(
        self,
        service: CallerIdentityService,
    ) -> CallerIdentityTokenExchangeProtocol:
        """Bind CallerIdentityService to core layer protocol."""
        return service

    @singleton
    @provider
    @inject
    def caller_token_provider_protocol(
        self,
        provider: CallerTokenProvider,
    ) -> CallerTokenProviderProtocol:
        """Bind CallerTokenProvider to core layer protocol."""
        return provider

    @singleton
    @provider
    @inject
    def caller_runtime_updater(
        self,
        baas_service: BaasService,
    ) -> CallerRuntimeUpdater:
        """Use the existing BaaS singleton for the Caller outbound PUT."""
        return baas_service

    @singleton
    @provider
    @inject
    def caller_runtime_updater_protocol(
        self,
        baas_service: BaasService,
    ) -> CallerRuntimeUpdaterProtocol:
        """Bind BaasService to core layer protocol."""
        return baas_service

    @singleton
    @provider
    @inject
    def caller_iam_token_service(
        self,
        caller_identity: CallerIdentityServiceProtocol,
        auth_plugin: AuthPlugin,
        token_provider: CallerTokenProvider,
        runtime_updater: CallerRuntimeUpdater,
        runtime_bindings: RuntimeBindingResolutionService,
        lock_repository: BotCollabLockRepositoryProtocol,
        session_validator: CallerSessionValidator,
    ) -> CallerIamTokenService:
        return CallerIamTokenService(
            caller_identity=caller_identity,
            auth_plugin=auth_plugin,
            token_provider=token_provider,
            runtime_updater=runtime_updater,
            runtime_bindings=runtime_bindings,
            lock_repository=lock_repository,
            session_validator=session_validator,
        )

    @singleton
    @provider
    @inject
    def token_plugin_pipeline(self) -> TokenPluginPipeline:
        """Community-safe empty pipeline; corp overrides this binding."""
        return NoopTokenPluginPipeline()

    @singleton
    @provider
    @inject
    def caller_iam_token_service_protocol(
        self,
        caller_service: CallerIamTokenService,
        token_pipeline: TokenPluginPipeline,
        bot_repository: BotRepository,
    ) -> CallerIamTokenServiceProtocol:
        """Expose Caller through a Caller-first independent plugin wrapper."""
        return TokenExchangeOrchestrator(
            caller_service=caller_service,
            token_pipeline=token_pipeline,
            bot_repository=bot_repository,
        )
