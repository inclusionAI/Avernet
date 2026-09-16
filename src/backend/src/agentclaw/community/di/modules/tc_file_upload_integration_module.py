"""DI wiring for bounded TC resource-ready notifications."""

from __future__ import annotations

from typing import Annotated

from injector import Module, inject, provider, singleton

from agentclaw.community.api.tc_resource_ready_observer import (
    TcResourceReadyObserverProtocol,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.protocols.platform import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)
from agentclaw.community.core.tc_file_upload_integrations.resource_context import (
    TcResourceContextService,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import QUALIFIER_GENERAL, HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugin_api.tc_resource_ready import (
    TcResourceReadyPublisherPlugin,
)
from agentclaw.community.plugins.community.tc_resource_ready import (
    HttpTcResourceReadyPublisher,
)
from agentclaw.community.plugins.local.tc_resource_ready import (
    LocalTcResourceReadyPublisher,
)
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()
_SINGLEBOX_LOCAL_AUTH = "singlebox-tc-file-service-token-local"


class TcFileUploadIntegrationModule(Module):
    def __init__(self, *, local: bool = False) -> None:
        self._local = local

    @singleton
    @provider
    @inject
    def tc_file_service_token(
        self,
        secret_resolver: SecretResolver,
        secret_names: cfg.SecretNamesConfig,
    ) -> cfg.TcFileServiceToken:
        secret_name = secret_names.tc_file_service_token
        if not secret_name:
            return cfg.TcFileServiceToken(
                value=_SINGLEBOX_LOCAL_AUTH if self._local else ""
            )
        try:
            resolved = secret_resolver.get_secret(secret_name=secret_name)
        except Exception:
            logger.exception(
                "[tc_file_upload_integration] token resolution failed for %r; "
                "returning empty token (failure-closed)",
                secret_name,
            )
            return cfg.TcFileServiceToken(value="")
        value = (
            getattr(resolved, "secret_value", None) if resolved is not None else None
        )
        return cfg.TcFileServiceToken(
            value=str(value)
            if value
            else (_SINGLEBOX_LOCAL_AUTH if self._local else "")
        )

    @singleton
    @provider
    @inject
    def tc_resource_ready_publisher(
        self,
        ecb_config: cfg.EcbConfig,
        auth_config: cfg.TcFileServiceToken,
        http_client: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> TcResourceReadyPublisherPlugin:
        if self._local:
            return LocalTcResourceReadyPublisher()
        base_url = ecb_config.resource_ready_base_url or (
            ecb_config.base_url_pre
            if get_current_env() == "pre"
            else ecb_config.base_url
        )
        return HttpTcResourceReadyPublisher(
            base_url=base_url,
            authorization_value=auth_config.value,
            http_client=http_client,
            timeout_seconds=ecb_config.resource_ready_timeout_seconds,
            worker_threads=ecb_config.resource_ready_worker_threads,
        )

    @singleton
    @provider
    @inject
    def tc_resource_ready_observer(
        self,
        publisher: TcResourceReadyPublisherPlugin,
        ecb_config: cfg.EcbConfig,
    ) -> TcResourceReadyObserverProtocol:
        return TcResourceReadyCoordinator(
            publisher=publisher,
            max_in_flight=ecb_config.resource_ready_max_in_flight,
            dedupe_ttl_seconds=ecb_config.resource_ready_dedupe_ttl_seconds,
            dedupe_max_entries=ecb_config.resource_ready_dedupe_max_entries,
        )

    @singleton
    @provider
    @inject
    def tc_resource_context_service(
        self,
        repository: SessionResourceRepositoryProtocol,
        token_vault: TokenVault,
    ) -> TcResourceContextService:
        return TcResourceContextService(repository, token_vault)
