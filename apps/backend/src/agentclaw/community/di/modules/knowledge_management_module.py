"""Public management composition; deployment overrides only config and identity."""

from typing import Annotated

import httpx
from agentclaw.community.adapters.knowledge_platform.catalog import (
    EcbKnowledgeClient,
    RemoteKnowledgeCatalog,
)
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.adapters.knowledge_platform.content import (
    RemoteKnowledgeContentService,
)
from agentclaw.community.adapters.knowledge_platform.content_client import (
    EcbKnowledgeContentClient,
)
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
)
from agentclaw.community.adapters.knowledge_platform.identity import (
    DirectKnowledgeIdentity,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.api.knowledge_management_service import (
    KnowledgeManagementServiceProtocol,
)
from agentclaw.community.core.knowledge_management.config import (
    KnowledgeManagementConfig,
)
from agentclaw.community.core.knowledge_management.ports import (
    KnowledgeCatalog,
    KnowledgeIdentityResolver,
)
from agentclaw.community.core.knowledge_management.repository.source import (
    ExistingKnowledgeEnablementReader,
    KnowledgeSourceRepository,
)
from agentclaw.community.core.knowledge_management.services.service import (
    KnowledgeManagementService,
)
from agentclaw.community.di.config import HttpClientPoolConfig
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.di.modules.http_client_module import _client
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.knowledge_session import KnowledgeSessionPlugin
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeSync
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from agentclaw.community.plugins.local.http_client import LocalHttpClient
from agentclaw.community.plugins.unavailable.knowledge_session import (
    DisabledKnowledgeSession,
)
from agentclaw.community.utils.env_utils import get_current_env
from injector import Module, provider, singleton


class KnowledgeManagementModule(Module):
    def __init__(self, profile: DeployProfile):
        self.profile = profile

    @provider
    @singleton
    def integration_config(self) -> KnowledgeIntegrationConfig:
        return KnowledgeIntegrationConfig(**_block("knowledge_integration"))

    @provider
    @singleton
    def config(self) -> KnowledgeManagementConfig:
        return KnowledgeManagementConfig(**_block("knowledge_management"))

    @provider
    @singleton
    def identities(self) -> KnowledgeIdentityResolver:
        return DirectKnowledgeIdentity()

    @provider
    @singleton
    def sessions(self) -> KnowledgeSessionPlugin:
        return DisabledKnowledgeSession()

    @provider
    @singleton
    def http(self, pool: HttpClientPoolConfig) -> Annotated[HttpClient, "knowledge"]:
        if self.profile in {DeployProfile.TEST, DeployProfile.CORP_TEST}:
            return LocalHttpClient(base_url="")
        return _client("", pool.for_qualifier("knowledge"))

    @provider
    @singleton
    def remote(
        self,
        config: KnowledgeIntegrationConfig,
        http: Annotated[HttpClient, "knowledge"],
    ) -> EcbKnowledgeClient:
        return EcbKnowledgeClient(config.catalog_origin, config, client=http)

    @provider
    @singleton
    def catalog(
        self,
        remote: EcbKnowledgeClient,
        identities: KnowledgeIdentityResolver,
        config: KnowledgeManagementConfig,
    ) -> KnowledgeCatalog:
        return RemoteKnowledgeCatalog(remote, identities, config.space_tenant)

    @provider
    @singleton
    def async_http(self, pool: HttpClientPoolConfig) -> AsyncContentTransport:
        policy = pool.for_qualifier("knowledge")
        transport = None
        if self.profile in {DeployProfile.TEST, DeployProfile.CORP_TEST}:

            def reject(request):
                raise AssertionError("Knowledge HTTP was not configured by this test")

            transport = httpx.MockTransport(reject)
        return AsyncHttpxClient(
            max_connections=policy.max_connections,
            max_keepalive_connections=policy.max_keepalive_connections,
            keepalive_expiry=policy.keepalive_expiry,
            http2=policy.http2,
            transport=transport,
        )

    @provider
    @singleton
    def content_client(
        self, remote: EcbKnowledgeClient, async_http: AsyncContentTransport
    ) -> EcbKnowledgeContentClient:
        return EcbKnowledgeContentClient(remote, async_http)

    @provider
    @singleton
    def content(
        self, client: EcbKnowledgeContentClient, sessions: KnowledgeSessionPlugin
    ) -> KnowledgeContentServiceProtocol:
        return RemoteKnowledgeContentService(client, sessions)

    @provider
    @singleton
    def source(
        self, db: DatabasePlugin, config: KnowledgeManagementConfig
    ) -> KnowledgeSourceRepository:
        return KnowledgeSourceRepository(db, get_current_env(), config.space_tenant)

    @provider
    @singleton
    def service(
        self,
        db: DatabasePlugin,
        source: KnowledgeSourceRepository,
        catalog: KnowledgeCatalog,
        sync: SpaceKnowledgeSync,
    ) -> KnowledgeManagementServiceProtocol:
        return KnowledgeManagementService(
            source,
            catalog,
            ExistingKnowledgeEnablementReader(db, get_current_env()),
            get_current_env(),
            sync,
        )
